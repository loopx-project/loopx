"""Shared CLI probe helpers for host-surface contract tests.

Every surface's test file drives the real `loopx.cli` entrypoint inside a
hermetic connected project; the plumbing — subprocess shape, registry
fixture, and the three generic contract probes (facade exit path, selection
gate, onboarding setup command) — is identical across surfaces, so it lives
here once instead of being copied per surface.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    subprocess_env = dict(os.environ if env is None else env)
    existing_pythonpath = subprocess_env.get("PYTHONPATH")
    subprocess_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPOSITORY_ROOT), existing_pythonpath) if part
    )
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env=subprocess_env,
    )


def connected_project(root: Path, goal_id: str = "surface-goal") -> Path:
    project = root / "project"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "test",
                        "status": "active",
                        "repo": str(project),
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return project


def start_goal_accepts_surface(surface: str, cwd: Path) -> dict:
    """The exact command the installed facade generates must run, not exit 2."""
    result = run_cli(
        "start-goal",
        "--guided",
        "--project",
        str(connected_project(cwd)),
        "--goal-id",
        "surface-goal",
        "--host-surface",
        surface,
        "--goal-text",
        f"verify the host contract for {surface}",
        cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["host_surface"] == surface
    activation = payload["command_pack"]["host_loop_activation"]
    assert activation["agent_type"] == surface
    return payload


def selection_gate_offers_surface(surface: str, cwd: Path) -> None:
    """The facade falls back to the selection gate when the host is unclear, so
    a host missing from the gate is unreachable even though it exists. The gate
    is only useful if its rerun_command is executable as-is."""
    gate_result = run_cli(
        "start-goal",
        "--guided",
        "--project",
        str(connected_project(cwd)),
        "--goal-id",
        "surface-goal",
        "--goal-text",
        "verify the host selection gate",
        cwd=cwd,
    )
    assert gate_result.returncode == 0, gate_result.stderr
    gate = json.loads(gate_result.stdout)["host_surface_selection_gate"]
    choices = {item["host_surface"]: item for item in gate["choices"]}
    assert surface in choices, sorted(choices)

    tokens = shlex.split(choices[surface]["rerun_command"])
    assert tokens[0] == "loopx"
    rerun = run_cli(*tokens[1:], cwd=cwd)
    assert rerun.returncode == 0, rerun.stderr
    assert json.loads(rerun.stdout)["host_surface"] == surface


def onboarding_setup_command_installs(
    surface: str,
    cwd: Path,
    env: dict[str, str],
    *,
    expected_skill: Path,
) -> dict:
    """agent-onboard hands back a setup command; executing it must provision
    the host it named, from any cwd. The surface's skills come from the LoopX
    installer, not from a host that manages skills itself."""
    node = shutil.which("node")
    assert node is not None, "The real CLI onboarding probe requires Node.js"
    env = {**env, "PATH": os.pathsep.join((str(Path(node).parent), env["PATH"]))}
    onboard = run_cli(
        "agent-onboard",
        "--agent-type",
        surface,
        "--project",
        str(connected_project(cwd)),
        "--goal-id",
        "surface-goal",
        cwd=cwd,
        env=env,
    )
    assert onboard.returncode == 0, onboard.stderr
    payload = json.loads(onboard.stdout)
    assert payload["agent_type"] == surface
    assert payload["skill_delivery"]["mode"] == "surface_managed"

    facade = payload["commands"]["install_command_facade"]
    assert facade is not None
    tokens = shlex.split(facade)
    assert tokens[0] == "loopx"
    install = run_cli(*tokens[1:], cwd=cwd, env=env)
    assert install.returncode == 0, install.stderr
    assert json.loads(install.stdout)["ok"] is True
    assert expected_skill.is_file()
    return payload
