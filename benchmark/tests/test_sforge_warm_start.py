"""Warm receiver composition; captured authority is never installed as current."""
import asyncio
from dataclasses import replace
import hashlib
import json
import shutil
import time
from types import SimpleNamespace

import pytest

from benchmark.runtime import sforge_warm_start as warm
from loopx.control_plane.effect_runtime import effect_runtime_result


@pytest.mark.parametrize("existing", ["empty", "nonempty", "foreign-link", "installed-link"])
def test_native_fork_skills_rejoin_ordinary_installer_without_erasing_contents(tmp_path, existing):
    from benchmark.runtime.codex import Execution, prepare_codex_home
    home, installed, foreign = (tmp_path / name for name in ("home", "installed", "foreign"))
    for path in (home, installed, foreign):
        path.mkdir()
    skills = home / "skills"
    if existing.endswith("link"):
        skills.symlink_to(installed if existing == "installed-link" else foreign)
    else:
        skills.mkdir()
        if existing == "nonempty":
            (skills / ".retained").write_text("must survive")
    warm._prepare_native_skills(home, installed)
    if existing == "nonempty":
        assert (home / ".loopx-native-skills-before-install" / ".retained").read_text() == "must survive"
    def ordinary_prepare():
        prepare_codex_home(home, execution=Execution(context="resume"), workspace=tmp_path,
            model="gpt-5", effort="high", base_url="http://localhost:1/v1", api_key="synthetic",
            wire_api="responses", skills=installed)
    if existing == "foreign-link":
        with pytest.raises(ValueError, match="skills changed"):
            ordinary_prepare()
        assert skills.resolve() == foreign
    else:
        ordinary_prepare()
        assert skills.is_symlink() and skills.resolve() == installed
        ordinary_prepare()


def test_native_skills_backup_never_overwrites_an_earlier_attempt(tmp_path):
    (tmp_path / "skills").mkdir()
    backup = tmp_path / ".loopx-native-skills-before-install"
    backup.mkdir()
    (backup / "retained").write_text("original")
    with pytest.raises(ValueError, match="backup already exists"):
        warm._prepare_native_skills(tmp_path, tmp_path)
    assert (backup / "retained").read_text() == "original"
    assert (tmp_path / "skills").is_dir()


@pytest.fixture
def point(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "candidate").write_text("retained candidate")
    (workspace / "candidate").chmod(0o600)
    config = tmp_path / "config.toml"
    config.write_text("[features]\ngoals = false\n")
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"snapshot": {"todos": [{"todo_id": "source-todo"}]}}))
    components = [{"id": "workspace", "source_path": str(workspace)},
                  {"id": "context", "source_path": str(context)}]
    metadata = {
        "native_session": {"guest_workspace": str(workspace), "guest_home": warm._CODEX_HOME,
                           "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "loopx_state": {"goal_id": "source-goal", "agent_id": "source-agent"},
        "worker_file_snapshot": {"components": components[:1]},
        "research_context": {"component_id": "context", "member_path": "",
                             "sha256": hashlib.sha256(context.read_bytes()).hexdigest()},
    }
    observed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    unit = tmp_path / "point"
    sealed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "point_metadata": metadata, "expected_source_sha256": observed["source_sha256"], "destination": str(unit)})
    start = warm.WarmStart(unit, sealed["sha256"], config, "new-goal", "new-agent", "source-todo", int(time.time()) + 600)
    return start, workspace


@pytest.mark.parametrize("change", [
    {"goal_id": "source-goal"}, {"agent_id": "source-agent"}, {"source_todo_id": "missing"},
    {"deadline_epoch": 1}, {"deadline_epoch": True}, {"normal_cadence": 0}, {"normal_cadence": True},
])
def test_invalid_participant_rejects_before_workspace_or_authority_changes(point, change):
    start, workspace = point
    before = (workspace / "candidate").read_bytes()
    with pytest.raises(ValueError):
        replace(start, **change).verify(str(workspace))
    assert (workspace / "candidate").read_bytes() == before
    assert not workspace.with_name("workspace.before-checkpoint").exists()


def test_restore_replaces_image_files_preserves_modes_and_refuses_second_attempt(point):
    start, workspace = point
    assert start.verify(str(workspace))
    (workspace / "stale-image-file").write_text("not captured")
    declaration = {"root": str(start.root), "sha256": start.sha256}
    warm.restore_workspace(declaration, workspace)
    assert not (workspace / "stale-image-file").exists()
    assert (workspace / "candidate").read_text() == "retained candidate"
    assert (workspace / "candidate").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already prepared"):
        warm.restore_workspace(declaration, workspace)
    assert workspace.with_name("workspace.before-checkpoint").is_dir()


def test_staging_preserves_sealed_modes(point, monkeypatch, tmp_path):
    start, workspace = point
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setattr(warm, "_CONTROL", str(control))
    commands = []
    class Environment:
        async def upload_dir(self, source, target):
            shutil.copytree(source, target)
        async def upload_file(self, source, target):
            shutil.copyfile(source, target)
        async def exec(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=0)
    asyncio.run(start.stage(Environment(), str(workspace)))
    assert "chmod -R" not in commands[0]
    effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(control / "restored-point"),
        "expected_sha256": start.sha256})
    declaration = json.loads((control / "warm-start.json").read_bytes())
    assert declaration["deadline_epoch"] == start.deadline_epoch


def test_normal_cadence_requires_accepted_current_replan_and_is_applied_once(tmp_path, monkeypatch):
    from benchmark.runtime import checkpoint_continuation, worker
    declaration = tmp_path / "warm.json"
    declaration.write_text(json.dumps({"sha256": "a" * 64, "normal_cadence": 6,
                                       "goal_id": "current", "agent_id": "receiver"}))
    env = {"LOOPX_WARM_START": str(declaration), "LOOPX_GOAL_ID": "current",
           "LOOPX_AGENT_ID": "receiver", "LOOPX_PROJECT": str(tmp_path)}
    monkeypatch.setattr(worker, "loopx_command", lambda env: ["loopx"])
    gate = {"capture_allowed": False, "checkpoint_sha256": "a" * 64}
    monkeypatch.setattr(checkpoint_continuation, "restored_capture_gate", lambda env: gate)
    calls = []
    def configure(command, **kwargs):
        calls.append(command)
        return '{"ok": true}'
    monkeypatch.setattr("subprocess.check_output", configure)
    assert warm.finish_preparation(env) == {"preparation_complete": False}
    assert not calls
    gate.update(capture_allowed=True, review_turn_id="accepted-current-replan")
    assert warm.finish_preparation(env)["preparation_complete"]
    assert warm.finish_preparation(env)["normal_cadence"] == 6
    assert len(calls) == 1
    with pytest.raises(ValueError, match="lineage"):
        warm.finish_preparation({**env, "LOOPX_AGENT_ID": "another-agent"})


def test_guest_helper_does_not_require_host_harbor_package():
    import subprocess
    import sys
    script = """import sys
class RejectHostDependency:
    def find_spec(self, name, path=None, target=None):
        if name == 'harbor' or name.startswith('harbor.'):
            raise AssertionError('guest imported a host-only package')
sys.meta_path.insert(0, RejectHostDependency())
from benchmark.runtime.sforge_warm_start import restore_workspace, prepare_context
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_overlay_workspace_backup_survives_cross_device_rename(point, monkeypatch):
    import errno
    start, workspace = point
    (workspace / "image-only").write_text("original image")
    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "overlay directory rename")
    monkeypatch.setattr("os.rename", cross_device)
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256}, workspace)
    backup = workspace.with_name("workspace.before-checkpoint")
    assert (backup / "image-only").read_text() == "original image"
    assert not (workspace / "image-only").exists()
    assert (workspace / "candidate").read_text() == "retained candidate"


@pytest.mark.parametrize("existing", ["none", "run", "attempt"])
def test_warm_launcher_reserves_host_attempt_without_preempting_native_run(tmp_path, monkeypatch, existing):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run as entry
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"destination": "/logs/agent/checkpoints", "point_metadata": {},
        "components": [{"id": "code", "source_path": "/task"}], "trigger": "fresh_replan",
        "native_session": True, "loopx_state": True, "evaluation_opportunity": True,
        "controller_handoff": True}))
    native_run = tmp_path / "runs" / "receiver"
    attempt = tmp_path / ".warm-attempts" / "receiver"
    if existing != "none":
        folder = native_run if existing == "run" else attempt
        folder.mkdir(parents=True)
        (folder / "retained").write_text("original")
    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "unused")
    class AdmissionBoundary(Exception):
        pass
    def at_source_check(*args):
        assert attempt.is_dir() and not native_run.exists()
        raise AdmissionBoundary()
    monkeypatch.setattr(entry, "source_pins", at_source_check)
    argv = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
        "--run-id", "receiver", "--worker", "heartbeat-explore", "--model", "fixture", "--effort", "high",
        "--judge-url", "http://localhost:1", "--checkpoint-request", str(request),
        "--checkpoint-controller-file", str(tmp_path / "controller.json"),
        "--restore-checkpoint", str(tmp_path / "point"), "--restore-sha256", "a" * 64,
        "--restore-config", str(tmp_path / "config.toml"), "--restore-goal", "new-goal",
        "--restore-agent", "new-agent", "--restore-source-todo", "source-todo"]
    with pytest.raises(AdmissionBoundary if existing == "none" else FileExistsError):
        entry.main(argv)
    if existing != "none":
        assert (folder / "retained").read_text() == "original"
    else:
        # An interrupted/uncertain attempt cannot silently obtain another budget.
        with pytest.raises(FileExistsError):
            entry.main(argv)
        assert not native_run.exists()
