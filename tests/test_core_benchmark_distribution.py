"""Installed Benchmark Toolkit over a checked Core task and an offline case pack."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest

from tests.test_core_distribution import GOAL, _assert_committed, _environment, _run
from tests.test_core_distribution import task as task


@pytest.fixture
def wheels():
    core = os.environ.get("LOOPX_CORE_TEST_WHEEL")
    toolkit = os.environ.get("LOOPX_BENCHMARK_TEST_WHEEL")
    if not core or not toolkit:
        pytest.skip("set LOOPX_CORE_TEST_WHEEL and LOOPX_BENCHMARK_TEST_WHEEL to the built artifacts")
    return Path(core).resolve(strict=True), Path(toolkit).resolve(strict=True)


@pytest.fixture
def core_python(tmp_path, wheels):
    uv = shutil.which("uv")
    assert uv, "uv is required to qualify a clean split installation"
    environment = tmp_path / "environment"
    subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True, timeout=45)
    python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    subprocess.run([uv, "pip", "install", "--offline", "--python", python, str(wheels[0])],
                   check=True, timeout=45)
    return python


def test_benchmark_wheel_owns_execution_and_keeps_the_catalog_in_core(wheels):
    with ZipFile(wheels[0]) as core, ZipFile(wheels[1]) as toolkit:
        assert not set(core.namelist()) & set(toolkit.namelist())
        assert "loopx/capabilities/benchmark_toolkit/catalog_entry.py" in core.namelist()
        assert "loopx/capabilities/benchmark_toolkit/four_arm_contract.py" in core.namelist()
        assert "loopx/capabilities/benchmark_toolkit/trajectory_assessment_store.py" in toolkit.namelist()
        assert "loopx/cli_commands/benchmark_dispatch.py" in toolkit.namelist()
        assert "loopx/cli.py" not in toolkit.namelist()


def _python_json(python, cwd, source):
    result = subprocess.run([python, "-I", "-c", source], cwd=cwd, env=_environment(python),
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


_IDENTITY = """import json
from loopx.release_manifest import release_runtime_identity
print(json.dumps(release_runtime_identity()))
"""


def test_installed_benchmark_reopens_the_same_analysis_after_removal(core_python, task, wheels, tmp_path):
    workspace, runtime, registry, arguments = task
    root = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    bench = [*root, "benchmark"]
    discover = [*bench, "trajectory-packs", "--goal-id", GOAL]
    before = _python_json(core_python, workspace, _IDENTITY)
    assert before["package_fingerprint"]
    assert "install the matching" in _run(core_python, discover, workspace, expected=2).stderr.lower()
    turn = json.loads(_run(core_python, arguments, workspace).stdout)
    turn_key = _assert_committed(core_python, workspace, runtime, turn)
    registry_bytes = registry.read_bytes()

    install = ["uv", "pip", "install", "--offline", "--python", core_python, str(wheels[1])]
    subprocess.run(install, check=True, timeout=45)
    probe = _python_json(core_python, workspace, """import contextlib, io, json, sys
from importlib import metadata
from pathlib import Path
from loopx.entrypoint import main
owner = metadata.distribution("loopx-capability-benchmark-toolkit")
assert owner.requires == [f"loopx-core=={metadata.version('loopx-core')}"]
with contextlib.redirect_stdout(io.StringIO()):
    assert main(["capability", "list", "--format", "json"]) == 0
    assert main(["commands", "--format", "json"]) == 0
assert "loopx.cli_commands.benchmark_dispatch" not in sys.modules
assert "loopx.capabilities.benchmark_toolkit.trajectory_assessment_store" not in sys.modules
assert "loopx.capabilities.benchmark_toolkit.native_codex_goal" not in sys.modules
record = next(record for record in owner.files if record.as_posix().endswith(".dist-info/METADATA"))
print(json.dumps({"metadata": str(Path(owner.locate_file(record))), "version": owner.version}))
""")
    detail = [*root, "capability", "show", "benchmark-toolkit"]
    record = json.loads(_run(core_python, detail, workspace).stdout)["capability"]
    assert record["provider_id"] == "loopx-capability-benchmark-toolkit"
    assert record["provider_state"] == {"declared": True, "installed": True, "enabled": True, "ready": True}
    current = _python_json(core_python, workspace, _IDENTITY)
    assert current["package_fingerprint"] != before["package_fingerprint"]
    assert json.loads(_run(core_python, discover, workspace).stdout)["packs"] == []

    path = Path(probe["metadata"])
    contents = path.read_bytes()
    try:
        path.write_bytes(contents.replace(f"Version: {probe['version']}".encode(), b"Version: 0.0.0", 1))
        rejected = json.loads(_run(core_python, detail, workspace).stdout)["capability"]["provider_state"]
        assert rejected["installed"] is rejected["ready"] is False
        _run(core_python, discover, workspace, expected=2)
    finally:
        path.write_bytes(contents)

    evidence = b"Independent validation found eight expected values.\n"
    source = tmp_path / "source"
    source.mkdir()
    (source / "check.txt").write_bytes(evidence)
    input_file = tmp_path / "input.json"
    payload = {
        "schema_version": "trajectory_assessment_input_v1",
        "scope": dict(goal_id=GOAL, case_id="checked-task", run_id="local-check", revision="fixed",
                      window="first accepted result"),
        "objective": "Read the original independently accepted task evidence.",
        "selection": dict(rule="One synthetic checked task", population_episodes=1, exhaustive=True),
        "sources": [dict(id="check", artifact="check.txt", artifact_sha256=sha256(evidence).hexdigest(),
                         first_line=1, text=evidence.decode().strip())],
        "episodes": [dict(id="episode", question="Did the independent check run?", source_ids=["check"],
                          eligible_metrics=["benefit_test"])],
    }
    register = [*bench, "trajectory-pack-register", "--goal-id", GOAL,
                "--input-json", str(input_file), "--source-root", str(source), "--title", "Original checked result"]
    payload["sources"][0]["artifact_sha256"] = "0" * 64
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    _run(core_python, register, workspace, expected=1)
    assert json.loads(_run(core_python, discover, workspace).stdout)["packs"] == []
    payload["sources"][0]["artifact_sha256"] = sha256(evidence).hexdigest()
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    pack = json.loads(_run(core_python, register, workspace).stdout)
    observed = json.loads(_run(core_python, discover, workspace).stdout)
    assert observed["default_pack_id"] == pack["pack_id"]
    assert [item["title"] for item in observed["packs"]] == ["Original checked result"]

    subprocess.run(["uv", "pip", "uninstall", "--python", core_python, "loopx-capability-benchmark-toolkit"],
                   check=True, timeout=45)
    assert _python_json(core_python, workspace, _IDENTITY) == before
    _run(core_python, discover, workspace, expected=2)
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", turn_key], workspace).stdout)
    assert not any(replay["effects"][name] for name in ("host_invoked", "state_written", "quota_spent"))
    assert _assert_committed(core_python, workspace, runtime, replay) == turn_key
    assert registry.read_bytes() == registry_bytes
    subprocess.run(install, check=True, timeout=45)
    assert json.loads(_run(core_python, discover, workspace).stdout) == observed


@pytest.mark.parametrize(
    "missing_member",
    [
        "loopx/cli_commands/benchmark_study.py",
        "loopx/capabilities/benchmark_toolkit/trajectory_assessment.py",
    ],
)
def test_partial_toolkit_install_reports_unavailable_until_repaired(
    core_python, task, wheels, tmp_path, missing_member,
):
    workspace, runtime, registry, arguments = task
    root = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    discover = [*root, "benchmark", "trajectory-packs", "--goal-id", GOAL]
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--python", core_python, str(wheels[1])],
        check=True,
        timeout=45,
    )
    probe = _python_json(core_python, workspace, f"""import json
from importlib import metadata
from pathlib import Path
owner = metadata.distribution("loopx-capability-benchmark-toolkit")
member = next(item for item in owner.files if item.as_posix() == {missing_member!r})
print(json.dumps({{"member_path": str(Path(owner.locate_file(member)))}}))
""")
    member_path = Path(probe["member_path"])
    backup_path = tmp_path / "removed_member.py"
    detail = [*root, "capability", "show", "benchmark-toolkit"]
    healthy_identity = _python_json(core_python, workspace, _IDENTITY)
    assert healthy_identity["package_fingerprint"].startswith("sha256:")
    assert json.loads(_run(core_python, detail, workspace).stdout)["capability"]["provider_state"]["ready"] is True
    assert _run(core_python, [*root, "benchmark", "--help"], workspace).returncode == 0
    assert json.loads(_run(core_python, discover, workspace).stdout)["packs"] == []

    try:
        member_path.rename(backup_path)
        partial = json.loads(_run(core_python, detail, workspace).stdout)["capability"]
        assert partial["provider_id"] == "loopx-capability-benchmark-toolkit"
        assert partial["provider_state"] == {
            "declared": True, "installed": False, "enabled": False, "ready": False,
        }
        assert "install the matching loopx-capability-benchmark-toolkit" in partial["next_real_step"].lower()
        partial_cmds = [
            entry["command"]
            for group in json.loads(_run(core_python, [*root, "commands"], workspace).stdout)["groups"]
            for entry in group["commands"]
        ]
        assert not any("loopx benchmark" in cmd for cmd in partial_cmds)
        rejected = _run(core_python, [*root, "benchmark", "--help"], workspace, expected=2)
        assert "install the matching loopx-capability-benchmark-toolkit" in rejected.stderr.lower()
        rejected_discover = _run(core_python, discover, workspace, expected=2)
        assert "install the matching loopx-capability-benchmark-toolkit" in rejected_discover.stderr.lower()
        broken_identity = _python_json(core_python, workspace, _IDENTITY)
        assert "package_fingerprint" not in broken_identity
        core_turn = json.loads(_run(core_python, arguments, workspace).stdout)
        _assert_committed(core_python, workspace, runtime, core_turn)
    finally:
        if backup_path.exists() and not member_path.exists():
            backup_path.rename(member_path)

    repaired = json.loads(_run(core_python, detail, workspace).stdout)["capability"]
    assert repaired["provider_id"] == "loopx-capability-benchmark-toolkit"
    assert repaired["provider_state"] == {
        "declared": True, "installed": True, "enabled": True, "ready": True,
    }
    repaired_cmds = [
        entry["command"]
        for group in json.loads(_run(core_python, [*root, "commands"], workspace).stdout)["groups"]
        for entry in group["commands"]
    ]
    assert any("loopx benchmark" in cmd for cmd in repaired_cmds)
    assert _run(core_python, [*root, "benchmark", "--help"], workspace).returncode == 0
    assert json.loads(_run(core_python, discover, workspace).stdout)["packs"] == []
    assert _python_json(core_python, workspace, _IDENTITY) == healthy_identity
