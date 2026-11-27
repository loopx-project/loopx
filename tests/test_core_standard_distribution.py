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


def _command_entries(python, workspace) -> list[str]:
    reference = json.loads(_run(python, ["commands", "--format", "json"], workspace).stdout)
    return [entry["command"] for group in reference["groups"] for entry in group["commands"]]


def _installation_doctor(python, workspace, *, deep: bool = False) -> dict:
    arguments = ["doctor", "--installation-only", *(["--deep"] if deep else []), "--format", "json"]
    return json.loads(_run(python, arguments, workspace).stdout)


def test_standard_installation_distinguishes_doctor_fingerprint_and_commands(
    core_python, standard_wheelhouse, tmp_path,
):
    bare_doctor = _installation_doctor(core_python, tmp_path)
    bare_fp = bare_doctor["service_runtime_identity"].get("package_fingerprint")
    assert bare_doctor["ok"] is True
    assert isinstance(bare_fp, str) and bare_fp.startswith("sha256:")
    bare_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" not in bare_commands
    assert "loopx issue-fix" not in bare_commands
    assert "loopx pr-review --help" not in bare_commands

    _install(core_python, standard_wheelhouse, "loopx-capability-issue-fix")
    issue_fix_doctor = _installation_doctor(core_python, tmp_path)
    issue_fix_fp = issue_fix_doctor["service_runtime_identity"].get("package_fingerprint")
    assert issue_fix_doctor["ok"] is True
    assert isinstance(issue_fix_fp, str) and issue_fix_fp.startswith("sha256:")
    assert issue_fix_fp != bare_fp
    issue_fix_commands = _command_entries(core_python, tmp_path)
    assert "loopx issue-fix" in issue_fix_commands
    assert "loopx agent-turn-recall" not in issue_fix_commands
    assert "loopx pr-review --help" not in issue_fix_commands

    _install(core_python, standard_wheelhouse, "loopx-core[standard]", "--reinstall-package", "loopx-core")
    standard_doctor = _installation_doctor(core_python, tmp_path)
    standard_fp = standard_doctor["service_runtime_identity"].get("package_fingerprint")
    assert standard_doctor["ok"] is True
    assert isinstance(standard_fp, str) and standard_fp.startswith("sha256:")
    assert standard_fp not in {bare_fp, issue_fix_fp}
    deep_doctor = _installation_doctor(core_python, tmp_path, deep=True)
    assert deep_doctor["ok"] is True
    standard_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" in standard_commands
    assert "loopx issue-fix" in standard_commands
    assert "loopx pr-review --help" in standard_commands

    # Tampered/unowned file under loopx/ must clear package_fingerprint even with [standard] installed.
    probe = subprocess.run(
        [core_python, "-I", "-c", "from pathlib import Path; import loopx; print(Path(loopx.__file__).parent)"],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, check=True, timeout=30,
    )
    unowned = Path(probe.stdout.strip()) / "unowned_standard_probe.py"
    unowned.write_text("# injected\n", encoding="utf-8")
    try:
        tampered_doctor = _installation_doctor(core_python, tmp_path)
        assert "package_fingerprint" not in tampered_doctor["service_runtime_identity"]
    finally:
        unowned.unlink(missing_ok=True)

    subprocess.run(["uv", "pip", "uninstall", "--python", core_python, *STANDARD_DISTRIBUTIONS],
                   check=True, timeout=45)
    restored_doctor = _installation_doctor(core_python, tmp_path)
    assert restored_doctor["service_runtime_identity"].get("package_fingerprint") == bare_fp
    restored_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" not in restored_commands
    assert "loopx issue-fix" not in restored_commands
    assert "loopx pr-review --help" not in restored_commands
