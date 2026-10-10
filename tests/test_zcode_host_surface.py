"""End-to-end host contract for the ZCode surface.

Installing discoverable files is not the same as being a usable LoopX host. The
generated `/loopx` facade tells the agent to run `start-goal ... --host-surface
<exact-current-host>`, so these tests execute that path for real: if zcode is
missing from the CLI choices, the selection gate or the activation dispatch,
the facade dead-ends at argparse and the surface is decorative.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from host_surface_cli_probes import (
    onboarding_setup_command_installs,
    selection_gate_offers_surface,
    start_goal_accepts_surface,
)

from loopx.agent_onboarding import _start_instruction, _surface_install_command
from loopx.host_loop_activation import (
    build_agent_type_catalog,
    build_host_loop_activation_packet,
    normalize_agent_type,
    scheduler_command_binding_for_agent_type,
)
from loopx.slash_command_install import install_slash_commands
from loopx.zcode_goal_mode import ZCODE_HOME_ENV, zcode_home

HOST_SURFACE = "zcode"


def test_start_goal_accepts_the_zcode_host_surface(tmp_path: Path) -> None:
    payload = start_goal_accepts_surface(HOST_SURFACE, tmp_path)
    activation = payload["command_pack"]["host_loop_activation"]
    assert activation["host_surface"] == "zcode_agent_loop"


def test_host_selection_gate_offers_zcode_and_its_rerun_command_works(
    tmp_path: Path,
) -> None:
    selection_gate_offers_surface(HOST_SURFACE, tmp_path)


def test_agent_onboarding_setup_command_installs_the_zcode_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOPX_SKILLS_DIR", raising=False)
    monkeypatch.delenv(ZCODE_HOME_ENV, raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        ZCODE_HOME_ENV: str(tmp_path / "zcode"),
    }
    if os.name == "nt":
        env["USERPROFILE"] = env["HOME"]
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        env["PATH"] = str(Path(shutil.which("node") or "").parent)
        env["PYTHONUTF8"] = "1"
    if "PYTHONPATH" in os.environ:  # keep hermetic when run from a worktree
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]

    onboarding_setup_command_installs(
        HOST_SURFACE,
        outside,
        env,
        expected_skill=tmp_path / "zcode" / "skills" / "loopx" / "SKILL.md",
    )


def test_zcode_home_env_override_wins_over_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv(ZCODE_HOME_ENV, str(tmp_path / "custom-zcode"))
    assert zcode_home() == tmp_path / "custom-zcode"
    monkeypatch.delenv(ZCODE_HOME_ENV, raising=False)
    assert zcode_home() == tmp_path / "home" / ".zcode"
    assert zcode_home(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_installer_preserves_user_owned_zcode_skill(tmp_path: Path) -> None:
    """The skills root is shared with the user's own skills; an unmarked file
    must never be overwritten, and a rerun over a managed file is a no-op."""
    skills_dir = tmp_path / "zcode" / "skills"
    skill_path = skills_dir / "loopx" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("user-owned skill body\n", encoding="utf-8")

    payload = install_slash_commands(
        execute=True,
        surfaces=["zcode"],
        zcode_home=str(tmp_path / "zcode"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("zcode", "/loopx")] == "skipped_user_file"
    assert skill_path.read_text(encoding="utf-8") == "user-owned skill body\n"

    skill_path.write_text(
        "<!-- loopx-managed-slash-command:v1 command=/loopx surface=claude-skills -->\nold\n",
        encoding="utf-8",
    )
    payload = install_slash_commands(
        execute=True,
        surfaces=["zcode"],
        zcode_home=str(tmp_path / "zcode"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("zcode", "/loopx")] == "updated"
    assert "user-owned skill body" not in skill_path.read_text(encoding="utf-8")

    retire = install_slash_commands(
        execute=True,
        uninstall=True,
        surfaces=["zcode"],
        zcode_home=str(tmp_path / "zcode"),
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
    assert entry["display_name"] == "ZCode"
    assert entry["host_loop"]
    assert entry["entry"] == "$loopx <task> or the LoopX skill from ZCODE_HOME/skills"
    # The bare product name is what a user types.
    assert HOST_SURFACE in entry["accepted_inputs"]
    assert normalize_agent_type("zcode") == HOST_SURFACE
    assert normalize_agent_type("z-code") == HOST_SURFACE
    assert scheduler_command_binding_for_agent_type(HOST_SURFACE) == {
        "runtime_profile": "generic_cli"
    }


def test_activation_keeps_skill_facade_default_and_exposes_explicit_native_opt_in() -> None:
    """The existing skill loop stays default; a separate native CLI binding is opt-in."""
    packet = build_host_loop_activation_packet(
        agent_type=HOST_SURFACE,
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    assert packet["activation_method"] == "run_agent_cli_loop_gated_by_quota"
    assert packet["host_mutation"]["cli_can_mutate_directly"] is False
    assert packet["host_mutation"]["host_loop_primitive"] is None
    assert packet["host_mutation"]["loop_driver"] == "agent_cli_turn_loop"
    assert (
        "The default ZCode entry remains the LoopX skill facade"
        in packet["host_mutation"]["missing_host_tool_gate"]
    )
    assert packet["setup_command"] == _surface_install_command(HOST_SURFACE, "loopx", ".")
    assert "quota should-run" in " ".join(packet["activation_steps"])
    assert "quota should-run" in _start_instruction(HOST_SURFACE)
    assert packet["entry_command_hint"] == "the LoopX skill installed in ZCODE_HOME/skills"

    native = packet["native_goal_provider"]
    assert native["default_off"] is True
    assert native["execution_mode"] == "managed_runtime"
    assert native["requires_explicit_host_selection"] is True
    assert set(native["commands"]) == {"bind", "start", "pause", "resume", "stop", "status"}
    assert "zcode-goal bind --goal-id surface-goal --agent-id probe-agent" in native["commands"]["bind"]
    assert "no per-model-call token limit" in native["quota_boundary"]


@pytest.mark.parametrize("agent_id", [None, "unregistered-agent"])
def test_legacy_skill_activation_does_not_advertise_an_unregistered_native_actor(agent_id: str | None) -> None:
    packet = build_host_loop_activation_packet(
        agent_type=HOST_SURFACE, goal_id="surface-goal", agent_id=agent_id,
        registered_agents=[],
    )
    assert packet["activation_allowed"] is True
    assert packet["activation_state"] == "legacy_unscoped"
    assert packet["commands"]["heartbeat_prompt_json"]
    assert packet["activation_method"] == "run_agent_cli_loop_gated_by_quota"
    assert packet["native_goal_provider"]["commands"] == {}


def test_native_activation_uses_the_existing_normalized_actor_selection() -> None:
    packet = build_host_loop_activation_packet(
        agent_type=HOST_SURFACE, goal_id="surface-goal",
        registered_agents=[" probe-agent ", "probe-agent"],
    )
    assert packet["agent_id"] == "probe-agent"
    assert packet["identity_contract"]["registered_agents"] == ["probe-agent"]
    assert "--agent-id probe-agent" in packet["native_goal_provider"]["commands"]["bind"]
