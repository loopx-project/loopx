"""Exercise the Standard installation over the same pending Core task."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_core_distribution import _assert_committed, _environment, _run
from tests.test_core_distribution import task as task


STANDARD_DISTRIBUTIONS = (
    "loopx-capability-agent-turn-recall",
    "loopx-capability-pull-request-review",
    "loopx-capability-issue-fix",
)


@pytest.fixture
def standard_wheelhouse():
    wheelhouse = os.environ.get("LOOPX_STANDARD_TEST_WHEELHOUSE")
    if not wheelhouse:
        pytest.skip("set LOOPX_STANDARD_TEST_WHEELHOUSE to the directory of matching wheels")
    return Path(wheelhouse).resolve(strict=True)


@pytest.fixture
def core_python(tmp_path, standard_wheelhouse):
    environment = tmp_path / "environment"
    subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True, timeout=45)
    python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    _install(python, standard_wheelhouse, "loopx-core")
    return python


def _install(python, wheelhouse, requirement, *options):
    subprocess.run(["uv", "pip", "install", "--offline", "--no-index", "--find-links", str(wheelhouse),
                    "--python", python, *options, requirement], check=True, timeout=45)


def _installed_loopx_packages(python, workspace):
    script = """
import json
from importlib.metadata import distributions
print(json.dumps({d.metadata["Name"]: d.version for d in distributions()
                  if d.metadata["Name"].startswith("loopx")}))
"""
    result = subprocess.run([python, "-I", "-c", script], cwd=workspace, env=_environment(python),
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def test_standard_installs_only_the_selected_capabilities_and_recovers_the_same_task(
    core_python, standard_wheelhouse, task,
):
    workspace, runtime, registry, arguments = task
    core = _installed_loopx_packages(core_python, workspace)
    assert core.keys() == {"loopx-core"}
    original = registry.read_bytes()

    _install(core_python, standard_wheelhouse, "loopx-core[standard]", "--reinstall-package", "loopx-core")
    assert _installed_loopx_packages(core_python, workspace) == {
        **core, **dict.fromkeys(STANDARD_DISTRIBUTIONS, core["loopx-core"]),
    }
    assert registry.read_bytes() == original
    script = """
import json, sys
from loopx.entrypoint import main

assert main(json.loads(sys.argv[1])) == 0
for module in ("loopx.capabilities.agent_turn_recall.core",
               "loopx.capabilities.pr_review_queue.core",
               "loopx.capabilities.issue_fix.workflow_plan"):
    assert module not in sys.modules, module
"""
    completed = subprocess.run([core_python, "-I", "-c", script, json.dumps(arguments)],
                               cwd=workspace, env=_environment(core_python),
                               capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    turn_key = _assert_committed(core_python, workspace, runtime, json.loads(completed.stdout))

    subprocess.run(["uv", "pip", "uninstall", "--python", core_python, *STANDARD_DISTRIBUTIONS],
                   check=True, timeout=45)
    assert _installed_loopx_packages(core_python, workspace) == core
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", turn_key], workspace).stdout)
    assert replay["replayed"] and not any(replay["effects"].values())
    assert _assert_committed(core_python, workspace, runtime, replay) == turn_key
