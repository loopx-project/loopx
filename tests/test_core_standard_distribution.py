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


def _installation_doctor(python, workspace, *, deep: bool = False, expected: int = 0) -> dict:
    arguments = ["doctor", "--installation-only", *(["--deep"] if deep else []), "--format", "json"]
    return json.loads(_run(python, arguments, workspace, expected=expected).stdout)


def test_standard_installation_distinguishes_doctor_fingerprint_and_commands(
    core_python, standard_wheelhouse, tmp_path,
):
    bare_doctor = _installation_doctor(core_python, tmp_path)
    bare_fp = bare_doctor["service_runtime_identity"].get("package_fingerprint")
    assert bare_doctor["ok"] is True
    assert "companion_distributions" not in bare_doctor
    assert isinstance(bare_fp, str) and bare_fp.startswith("sha256:")
    bare_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" not in bare_commands
    assert "loopx issue-fix" not in bare_commands
    assert "loopx pr-review --help" not in bare_commands

    _install(core_python, standard_wheelhouse, "loopx-capability-issue-fix")
    issue_fix_doctor = _installation_doctor(core_python, tmp_path)
    issue_fix_fp = issue_fix_doctor["service_runtime_identity"].get("package_fingerprint")
    assert issue_fix_doctor["ok"] is True
    issue_fix_companions = {
        item["distribution"]: item for item in issue_fix_doctor["companion_distributions"]
    }
    assert set(issue_fix_companions) == {"loopx-capability-issue-fix"}
    assert issue_fix_companions["loopx-capability-issue-fix"]["ready"] is True
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
    standard_companions = {
        item["distribution"]: item for item in standard_doctor["companion_distributions"]
    }
    assert set(standard_companions) == set(STANDARD_DISTRIBUTIONS)
    assert all(
        item["ready"] is True
        and item["version"] == standard_doctor["service_runtime_identity"]["package_version"]
        for item in standard_companions.values()
    )
    standard_checks = {check["id"]: check for check in standard_doctor["checks"]}
    assert standard_checks["companion_distributions_compatible"]["required"] is True
    assert standard_checks["companion_distributions_compatible"]["ok"] is True
    assert isinstance(standard_fp, str) and standard_fp.startswith("sha256:")
    assert standard_fp not in {bare_fp, issue_fix_fp}
    deep_doctor = _installation_doctor(core_python, tmp_path, deep=True)
    assert deep_doctor["ok"] is True
    standard_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" in standard_commands
    assert "loopx issue-fix" in standard_commands
    assert "loopx pr-review --help" in standard_commands

    # Tampered/unowned file under loopx/ must fail installation doctor and clear package_fingerprint even with [standard] installed.
    probe = subprocess.run(
        [core_python, "-I", "-c", "from pathlib import Path; import loopx; print(Path(loopx.__file__).parent)"],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, check=True, timeout=30,
    )
    package_dir = Path(probe.stdout.strip())
    unowned = package_dir / "unowned_standard_probe.py"
    unowned.write_text("# injected\n", encoding="utf-8")
    try:
        tampered_doctor = _installation_doctor(core_python, tmp_path, expected=1)
        assert tampered_doctor["ok"] is False
        assert "package_fingerprint" not in tampered_doctor["service_runtime_identity"]
        tampered_checks = {check["id"]: check for check in tampered_doctor["checks"]}
        assert tampered_checks["package_runtime_fingerprint"]["required"] is True
        assert tampered_checks["package_runtime_fingerprint"]["ok"] is False
        assert "loopx-core" in tampered_doctor["fix"]
        assert "loopx-capability-research" not in tampered_doctor["fix"]
        for dist_name in STANDARD_DISTRIBUTIONS:
            assert dist_name in tampered_doctor["fix"]
    finally:
        unowned.unlink(missing_ok=True)

    # Missing module inside an installed [standard] companion must also fail companion diagnostics and clear package_fingerprint.
    workflow_plan = package_dir / "capabilities/issue_fix/workflow_plan.py"
    workflow_backup = workflow_plan.read_bytes()
    workflow_plan.unlink()
    try:
        damaged_doctor = _installation_doctor(core_python, tmp_path, expected=1)
        assert damaged_doctor["ok"] is False
        assert "package_fingerprint" not in damaged_doctor["service_runtime_identity"]
        damaged_checks = {check["id"]: check for check in damaged_doctor["checks"]}
        assert damaged_checks["companion_distributions_compatible"]["required"] is True
        assert damaged_checks["companion_distributions_compatible"]["ok"] is False
        assert "loopx-capability-issue-fix" in damaged_checks["companion_distributions_compatible"]["detail"]
        assert damaged_checks["package_runtime_fingerprint"]["required"] is True
        assert damaged_checks["package_runtime_fingerprint"]["ok"] is False
        damaged_companions = {
            item["distribution"]: item for item in damaged_doctor["companion_distributions"]
        }
        assert damaged_companions["loopx-capability-issue-fix"]["ready"] is False
        assert "missing_files" in damaged_companions["loopx-capability-issue-fix"]["reasons"]
        assert "loopx-capability-issue-fix" in damaged_doctor["fix"]
    finally:
        workflow_plan.write_bytes(workflow_backup)

    # Corrupt companion metadata (direct_url.json) must preserve structured JSON diagnostics and fail closed.
    dist_info_probe = subprocess.run(
        [
            core_python,
            "-I",
            "-c",
            (
                "from importlib.metadata import distribution; "
                "from pathlib import Path; "
                "dist = distribution('loopx-capability-issue-fix'); "
                "record = next(f for f in (dist.files or []) if f.as_posix().endswith('.dist-info/METADATA')); "
                "print(Path(dist.locate_file(record)).parent / 'direct_url.json')"
            ),
        ],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, check=True, timeout=30,
    )
    direct_url_path = Path(dist_info_probe.stdout.strip())
    direct_url_had_file = direct_url_path.exists()
    direct_url_backup = direct_url_path.read_bytes() if direct_url_had_file else None
    direct_url_path.write_text("{corrupt-json", encoding="utf-8")
    try:
        corrupt_doctor = _installation_doctor(core_python, tmp_path, expected=1)
        assert corrupt_doctor["ok"] is False
        assert "package_fingerprint" not in corrupt_doctor["service_runtime_identity"]
        corrupt_checks = {check["id"]: check for check in corrupt_doctor["checks"]}
        assert corrupt_checks["companion_distributions_compatible"]["required"] is True
        assert corrupt_checks["companion_distributions_compatible"]["ok"] is False
        assert "loopx-capability-issue-fix" in corrupt_checks["companion_distributions_compatible"]["detail"]
        assert corrupt_checks["package_runtime_fingerprint"]["required"] is True
        assert corrupt_checks["package_runtime_fingerprint"]["ok"] is False
        corrupt_companions = {
            item["distribution"]: item for item in corrupt_doctor["companion_distributions"]
        }
        assert set(corrupt_companions) == set(STANDARD_DISTRIBUTIONS)
        assert corrupt_companions["loopx-capability-issue-fix"]["ready"] is False
        assert "invalid_companion_metadata" in corrupt_companions["loopx-capability-issue-fix"]["reasons"]
        assert corrupt_companions["loopx-capability-agent-turn-recall"]["ready"] is True
        assert corrupt_companions["loopx-capability-pull-request-review"]["ready"] is True
        assert "loopx-capability-issue-fix" in corrupt_doctor["fix"]
    finally:
        if direct_url_had_file and direct_url_backup is not None:
            direct_url_path.write_bytes(direct_url_backup)
        else:
            direct_url_path.unlink(missing_ok=True)

    restored_from_damage = _installation_doctor(core_python, tmp_path)
    assert restored_from_damage["ok"] is True
    assert restored_from_damage["service_runtime_identity"].get("package_fingerprint") == standard_fp

    subprocess.run(["uv", "pip", "uninstall", "--python", core_python, *STANDARD_DISTRIBUTIONS],
                   check=True, timeout=45)
    restored_doctor = _installation_doctor(core_python, tmp_path)
    assert restored_doctor["ok"] is True
    assert "companion_distributions" not in restored_doctor
    assert restored_doctor["service_runtime_identity"].get("package_fingerprint") == bare_fp
    restored_commands = _command_entries(core_python, tmp_path)
    assert "loopx agent-turn-recall" not in restored_commands
    assert "loopx issue-fix" not in restored_commands
    assert "loopx pr-review --help" not in restored_commands
