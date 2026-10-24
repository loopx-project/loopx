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
from loopx.control_plane.effect_runtime import EffectRuntimeConflict, effect_runtime_result


@pytest.mark.parametrize("extension", ["-1", "64800", str(2 ** 53)])
def test_extension_without_a_warm_entry_refuses_before_attempt_or_registration(tmp_path, extension):
    pytest.importorskip("harbor")
    from benchmark.edgebench import run as entry
    argv = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
        "--run-id", "unadmitted", "--worker", "heartbeat-explore", "--model", "fixture",
        "--effort", "high", "--judge-url", "http://localhost:1",
        "--restore-phase-extension-seconds", extension]
    with pytest.raises(SystemExit) as rejected:
        entry.main(argv)
    assert rejected.value.code == 2 and not list(tmp_path.iterdir())


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
    config.write_text('model="original"\nmodel_reasoning_effort="high"\n[features]\ngoals = false\n')
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
    {"fresh_replan": "false"},
])
def test_invalid_participant_rejects_before_workspace_or_authority_changes(point, change):
    start, workspace = point
    before = (workspace / "candidate").read_bytes()
    with pytest.raises(ValueError):
        replace(start, **change).verify(str(workspace))
    assert (workspace / "candidate").read_bytes() == before
    assert not workspace.with_name("workspace.before-checkpoint").exists()


@pytest.mark.parametrize("remaining, expected_budget", [(600, 60), (7, 7)])
def test_full_checkpoint_read_budget_is_bounded_by_original_deadline(point, monkeypatch, remaining, expected_budget):
    start, workspace = point
    now = time.time()
    start = replace(start, deadline_epoch=int(now) + remaining)
    monkeypatch.setattr(warm, "time", SimpleNamespace(time=lambda: float(int(now))))
    calls = []
    def verify(method, params, **kwargs):
        calls.append((method, params, kwargs))
        return effect_runtime_result(method, params, **kwargs)
    monkeypatch.setattr(warm, "effect_runtime_result", verify)
    assert start.verify(str(workspace))
    assert len(calls) == 1
    method, params, kwargs = calls[0]
    assert method == "runtime.checkpoint_artifacts" and params["action"] == "verify"
    assert kwargs["timeout"] == expected_budget
    assert not workspace.with_name("workspace.before-checkpoint").exists()


def test_verification_cannot_admit_a_checkpoint_after_its_deadline(point, monkeypatch):
    start, workspace = point
    clock = [float(int(time.time()))]
    start = replace(start, deadline_epoch=int(clock[0]) + 7)
    monkeypatch.setattr(warm, "time", SimpleNamespace(time=lambda: clock[0]))
    def verify(method, params, **kwargs):
        value = effect_runtime_result(method, params, **kwargs)
        clock[0] += 8
        return value
    monkeypatch.setattr(warm, "effect_runtime_result", verify)
    with pytest.raises(ValueError, match="expired during checkpoint verification"):
        start.verify(str(workspace))
    assert (workspace / "candidate").read_text() == "retained candidate"
    assert not workspace.with_name("workspace.before-checkpoint").exists()


def test_expiry_during_context_validation_cannot_admit_the_checkpoint(point, monkeypatch):
    start, workspace = point
    clock = [float(int(time.time()))]
    start = replace(start, deadline_epoch=int(clock[0]) + 7)
    monkeypatch.setattr(warm, "time", SimpleNamespace(time=lambda: clock[0]))
    context = (start.root / "components/context").read_bytes()
    def digest(data):
        result = hashlib.sha256(data)
        if data == context:
            clock[0] += 8
        return result
    monkeypatch.setattr(warm, "hashlib", SimpleNamespace(sha256=digest))
    with pytest.raises(ValueError, match="expired during checkpoint verification"):
        start.verify(str(workspace))


def test_restore_replaces_image_files_preserves_modes_and_refuses_second_attempt(point):
    start, workspace = point
    assert start.verify(str(workspace))
    (workspace / "stale-image-file").write_text("not captured")
    declaration = {"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch}
    warm.restore_workspace(declaration, workspace)
    assert not (workspace / "stale-image-file").exists()
    assert (workspace / "candidate").read_text() == "retained candidate"
    assert (workspace / "candidate").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already prepared"):
        warm.restore_workspace(declaration, workspace)
    assert workspace.with_name("workspace.before-checkpoint").is_dir()


@pytest.mark.parametrize("model_transition", [False, True])
def test_staging_preserves_sealed_modes(point, monkeypatch, tmp_path, model_transition):
    start, workspace = point
    if model_transition:
        destination = tmp_path / "destination.toml"
        destination.write_text(start.config.read_text().replace('"original"', '"next"')
                               .replace('"high"', '"max"'))
        start = replace(start, destination_config=destination)
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
    assert asyncio.run(start.stage(Environment(), str(workspace))) == []
    assert "chmod -R" not in commands[0]
    effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(control / "restored-point"),
        "expected_sha256": start.sha256})
    declaration = json.loads((control / "warm-start.json").read_bytes())
    assert declaration["deadline_epoch"] == start.deadline_epoch
    if model_transition:
        assert (control / "restored-model-config.toml").read_bytes() == destination.read_bytes()
        assert declaration["destination_config"] == str(control / "restored-model-config.toml")
        assert (control / "restored-config.toml").read_bytes() == start.config.read_bytes()
    else:
        assert "destination_config" not in declaration


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
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch}, workspace)
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


@pytest.fixture
def research_point(point, tmp_path, monkeypatch, request):
    start, workspace = point
    research = tmp_path / "retained-experiments"
    if getattr(request, "param", "directory") == "file":
        research.write_text("retained observations")
        research.chmod(0o600)
    else:
        research.mkdir()
        research.chmod(0o700)
        (research / "candidate.py").write_text("def predict(x): return x * 7\n")
        (research / "observations.json").write_text('[2, 3, 5]')
        (research / "candidate.py").chmod(0o600)
    manifest = json.loads((start.root / "checkpoint.json").read_bytes())
    components = manifest["components"] + [{"id": "experiments", "source_path": str(research)}]
    metadata = manifest["point_metadata"]
    metadata["worker_file_snapshot"]["components"].append(components[-1])
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    unit = tmp_path / "research-point"
    result = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "point_metadata": metadata, "expected_source_sha256": probe["source_sha256"], "destination": str(unit)})
    # Only the fixture source; the receiver must use sealed bytes.
    if research.is_dir():
        shutil.rmtree(research)
    else:
        research.unlink()
    monkeypatch.setattr(warm, "_RESEARCH_ROOTS", (tmp_path,))
    return replace(start, root=unit, sha256=result["sha256"], research_component_ids=("experiments",)), workspace, research


def test_explicit_research_files_reproduce_the_retained_experiment(research_point):
    start, workspace, research = research_point
    assert start.verify(str(workspace))
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                            "research_component_ids": ["experiments"]}, workspace)
    scope = {}
    exec((research / "candidate.py").read_text(), scope)
    assert [scope["predict"](x) for x in json.loads((research / "observations.json").read_text())] == [14, 21, 35]
    assert (research / "candidate.py").stat().st_mode & 0o777 == 0o600
    # The original unit is still valid and replay cannot overwrite receiver work.
    effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(start.root),
                                                          "expected_sha256": start.sha256})
    with pytest.raises(ValueError, match="already exists"):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                                "research_component_ids": ["experiments"]}, workspace)


def test_unselected_research_files_remain_inert(research_point):
    start, workspace, research = research_point
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch}, workspace)
    assert not research.exists()


@pytest.mark.parametrize("selected", [("absent",), ("workspace",), ("context",),
                                       ("experiments", "experiments"), (None,), "experiments"])
def test_invalid_research_selection_rejects_before_workspace_changes(research_point, selected):
    start, workspace, research = research_point
    with pytest.raises(ValueError):
        replace(start, research_component_ids=selected).verify(str(workspace))
    with pytest.raises(ValueError):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                                "research_component_ids": selected}, workspace)
    assert (workspace / "candidate").read_text() == "retained candidate"
    assert not workspace.with_name("workspace.before-checkpoint").exists() and not research.exists()


@pytest.mark.parametrize("collision", ["file", "directory", "symlink", "missing-parent", "parent-symlink"])
def test_research_destination_conflicts_preserve_receiver(research_point, collision, tmp_path):
    start, workspace, research = research_point
    if collision == "file":
        research.write_text("unrelated")
    elif collision == "directory":
        research.mkdir()
    elif collision == "symlink":
        research.symlink_to(tmp_path / "missing")
    else:
        # Exercise destination validation directly: the sealed source path must
        # never be remapped just to make a receiver pass.
        parent = tmp_path / "new-parent"
        if collision == "parent-symlink":
            parent.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(ValueError, match="physical parent"):
            warm._check_research_destinations([("experiments", start.root, parent / "new-file")])
        return
    with pytest.raises(ValueError, match="already exists"):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                                "research_component_ids": ["experiments"]}, workspace)
    assert not workspace.with_name("workspace.before-checkpoint").exists()
    if collision == "file":
        assert research.read_text() == "unrelated"


def test_research_data_cannot_target_provider_control_or_unqualified_roots(research_point, monkeypatch):
    start, workspace, _ = research_point
    metadata = json.loads((start.root / "checkpoint.json").read_bytes())["point_metadata"]
    monkeypatch.setattr(warm, "_RESEARCH_ROOTS", (warm.Path("/tmp"), warm.Path("/home/workspace")))
    entry = metadata["worker_file_snapshot"]["components"][-1]
    for path in ("/", "/tmp", "/home/workspace", "/etc/config", warm._CONTROL + "/registry.json",
                 warm._CODEX_HOME, "/logs/agent", "/tmp/a/../b"):
        entry["source_path"] = path
        with pytest.raises(ValueError, match="separate data"):
            warm._research_components(start.root, metadata, workspace, ("experiments",))


def test_research_copy_mismatch_is_an_unusable_attempt(research_point, monkeypatch):
    start, workspace, research = research_point
    copy = shutil.copytree
    def corrupt(source, destination, *args, **kwargs):
        result = copy(source, destination, *args, **kwargs)
        if destination == research:
            (research / "observations.json").write_text('[999]')
        return result
    monkeypatch.setattr(shutil, "copytree", corrupt)
    with pytest.raises(ValueError, match="differ from the sealed"):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                                "research_component_ids": ["experiments"]}, workspace)
    assert workspace.with_name("workspace.before-checkpoint").is_dir()


@pytest.mark.parametrize("research_point", ["directory", "file"], indirect=True)
def test_research_selection_survives_staging(research_point, monkeypatch, tmp_path):
    start, workspace, research = research_point
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setattr(warm, "_CONTROL", str(control))
    class Environment:
        async def upload_dir(self, source, target):
            shutil.copytree(source, target)
        async def upload_file(self, source, target):
            shutil.copyfile(source, target)
        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=0)
    assert asyncio.run(start.stage(Environment(), str(workspace))) == [str(research)]
    declaration = json.loads((control / "warm-start.json").read_bytes())
    assert declaration["research_component_ids"] == ["experiments"]


@pytest.mark.parametrize("options, error", [
    (["--restore-research-component", "experiments"], "Research component restoration requires"),
    (["--restore-fresh-after-replan"], "unrecognized arguments: --restore-fresh-after-replan"),
    (["--restore-fresh-replan"], "Fresh context requires an explicit warm checkpoint continuation"),
])
def test_restore_options_reject_before_registration(tmp_path, capsys, options, error):
    pytest.importorskip("harbor")
    from benchmark.edgebench import run as entry
    with pytest.raises(SystemExit) as rejected:
        entry.main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "unadmitted", "--worker", "heartbeat-explore", "--model", "fixture",
            "--effort", "high", "--judge-url", "http://localhost:1",
            *options])
    assert rejected.value.code == 2 and not list(tmp_path.iterdir())
    assert error in capsys.readouterr().err


@pytest.mark.parametrize("research_point", ["file"], indirect=True)
def test_standalone_research_file_keeps_bytes_and_mode(research_point):
    start, workspace, research = research_point
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch,
                            "research_component_ids": ["experiments"]}, workspace)
    assert research.read_text() == "retained observations"
    assert research.stat().st_mode & 0o777 == 0o600


def test_workspace_backup_cannot_be_a_research_destination(research_point):
    start, workspace, _ = research_point
    metadata = json.loads((start.root / "checkpoint.json").read_bytes())["point_metadata"]
    metadata["worker_file_snapshot"]["components"][-1]["source_path"] = str(workspace.with_name("workspace.before-checkpoint"))
    with pytest.raises(ValueError, match="separate data"):
        warm._research_components(start.root, metadata, workspace, ("experiments",))


@pytest.mark.parametrize('remaining, expected', [(600, 90), (12, 12)])
def test_native_admission_full_history_allowance_uses_original_phase(monkeypatch, remaining, expected):
    from loopx.hosts import codex_checkpoint
    now = 1000
    monkeypatch.setattr(warm, 'time', SimpleNamespace(time=lambda: now))
    calls = []
    def admit(*args, **kwargs):
        calls.append((args, kwargs))
        return {'session_id': 'verified-native-fork'}
    monkeypatch.setattr(codex_checkpoint, 'admit_native_checkpoint_session', admit)
    declaration = {'root': '/point', 'sha256': 'a' * 64, 'config': '/config.toml',
                   'deadline_epoch': now + remaining}
    env = {'LOOPX_CODEX_SANDBOX': 'danger-full-access'}
    assert warm.admit_warm_session(env, declaration)['session_id'] == 'verified-native-fork'
    assert calls[0][1]['timeout'] == expected
    assert declaration['deadline_epoch'] == now + remaining


@pytest.mark.parametrize('deadline', [True, None, '1001', 1000, 999, 2 ** 53])
def test_native_admission_rejects_expired_or_invalid_phase_before_io(monkeypatch, deadline):
    from loopx.hosts import codex_checkpoint
    monkeypatch.setattr(warm, 'time', SimpleNamespace(time=lambda: 1000))
    monkeypatch.setattr(codex_checkpoint, 'admit_native_checkpoint_session',
                        lambda *a, **k: pytest.fail('Native IO after invalid deadline'))
    with pytest.raises(ValueError):
        warm.admit_warm_session({}, {'deadline_epoch': deadline})


def test_native_admission_cannot_return_success_after_original_phase(monkeypatch):
    from loopx.hosts import codex_checkpoint
    clock = [1000]
    monkeypatch.setattr(warm, 'time', SimpleNamespace(time=lambda: clock[0]))
    def admit(*args, **kwargs):
        clock[0] = 1002
        return {'session_id': 'too-late'}
    monkeypatch.setattr(codex_checkpoint, 'admit_native_checkpoint_session', admit)
    with pytest.raises(ValueError, match='allowance expired'):
        warm.admit_warm_session({'LOOPX_CODEX_SANDBOX': 'danger-full-access'},
            {'root': '/point', 'sha256': 'a' * 64, 'config': '/config.toml', 'deadline_epoch': 1001})


@pytest.mark.parametrize("remaining, expected", [(600, 60), (7, 7)])
def test_workspace_verification_uses_bounded_full_point_read(point, monkeypatch, remaining, expected):
    start, workspace = point
    monkeypatch.setattr(warm, "time", SimpleNamespace(time=lambda: 1000))
    original = warm.effect_runtime_result
    calls = []
    def verify(method, params, **kwargs):
        calls.append(kwargs["timeout"])
        return original(method, params, **kwargs)
    monkeypatch.setattr(warm, "effect_runtime_result", verify)
    warm.restore_workspace({"root": str(start.root), "sha256": start.sha256,
                            "deadline_epoch": 1000 + remaining}, workspace)
    assert calls == [expected]
    assert (workspace / "candidate").read_text() == "retained candidate"


def test_expired_workspace_verification_preserves_image_before_mutation(point, monkeypatch):
    start, workspace = point
    clock = [1000]
    monkeypatch.setattr(warm, "time", SimpleNamespace(time=lambda: clock[0]))
    original = warm.effect_runtime_result
    def verify(method, params, **kwargs):
        result = original(method, params, **kwargs)
        clock[0] = 1002
        return result
    monkeypatch.setattr(warm, "effect_runtime_result", verify)
    with pytest.raises(ValueError, match="allowance expired"):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256,
                                "deadline_epoch": 1001}, workspace)
    assert not workspace.with_name("workspace.before-checkpoint").exists()
    assert (workspace / "candidate").read_text() == "retained candidate"


@pytest.mark.parametrize("legacy", [True, False, "false"])
def test_removed_post_replan_declaration_rejects_before_native_effects(tmp_path, legacy):
    from benchmark.runtime.checkpoint_session_refresh import prepare_session_refresh
    declaration = tmp_path / "warm.json"
    declaration.write_text(json.dumps({"fresh_after_replan": legacy, "fresh_replan": True}))
    # No usable session: rejection must precede all native/session access.
    with pytest.raises(ValueError, match="no longer supported"):
        prepare_session_refresh({"LOOPX_WARM_START": str(declaration)}, None, tmp_path)


def test_omitted_fresh_replan_keeps_default_native_history(tmp_path):
    from benchmark.runtime.checkpoint_session_refresh import prepare_session_refresh
    declaration = tmp_path / "warm.json"
    declaration.write_text("{}")
    assert prepare_session_refresh({"LOOPX_WARM_START": str(declaration)}, None, tmp_path) == ""


@pytest.fixture
def parent_workspace_point(point, tmp_path, request):
    start, original = point
    parent = tmp_path / "captured-parent"
    workspace = parent / "nested" / "task"
    workspace.parent.mkdir(parents=True)
    shutil.copytree(original, workspace)
    (parent / "sibling-report").write_text("inert captured sibling")
    manifest = json.loads((start.root / "checkpoint.json").read_bytes())
    metadata = manifest["point_metadata"]
    metadata["native_session"]["guest_workspace"] = str(workspace)
    component = {"id": "workspace", "source_path": str(parent)}
    metadata["worker_file_snapshot"]["components"] = [component]
    variant = getattr(request, "param", "valid")
    if variant == "ambiguous":
        metadata["worker_file_snapshot"]["components"].append(
            {"id": "context", "source_path": str(workspace.parent)})
    elif variant == "missing":
        metadata["native_session"]["guest_workspace"] = str(parent / "absent")
        workspace = parent / "absent"
    elif variant == "noncanonical":
        metadata["worker_file_snapshot"]["components"] = [
            {"id": "workspace", "source_path": str(parent) + "/"}]
    components = [component, manifest["components"][1]]
    observed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    unit = tmp_path / "parent-point"
    sealed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "point_metadata": metadata, "expected_source_sha256": observed["source_sha256"], "destination": str(unit)})
    shutil.rmtree(parent)
    workspace.mkdir(parents=True)
    (workspace / "candidate").write_text("fresh image candidate")
    (parent / "sibling-report").write_text("receiver sibling must survive")
    return replace(start, root=unit, sha256=sealed["sha256"]), workspace, parent


def test_parent_capture_restores_only_native_workspace_and_preserves_point(parent_workspace_point):
    start, workspace, parent = parent_workspace_point
    assert start.verify(str(workspace))
    declaration = {"root": str(start.root), "sha256": start.sha256, "deadline_epoch": start.deadline_epoch}
    warm.restore_workspace(declaration, workspace)
    assert (workspace / "candidate").read_text() == "retained candidate"
    assert (workspace / "candidate").stat().st_mode & 0o777 == 0o600
    assert (workspace.with_name("task.before-checkpoint") / "candidate").read_text() == "fresh image candidate"
    assert (parent / "sibling-report").read_text() == "receiver sibling must survive"
    assert not (workspace / "sibling-report").exists()
    effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(start.root),
                                                          "expected_sha256": start.sha256})
    with pytest.raises(ValueError, match="already prepared"):
        warm.restore_workspace(declaration, workspace)


@pytest.mark.parametrize("parent_workspace_point", ["ambiguous", "missing", "noncanonical"], indirect=True)
def test_invalid_parent_capture_refuses_preflight_and_restore_before_mutation(parent_workspace_point):
    start, workspace, parent = parent_workspace_point
    with pytest.raises(ValueError):
        start.verify(str(workspace))
    with pytest.raises(ValueError):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256,
                                "deadline_epoch": start.deadline_epoch}, workspace)
    assert (workspace / "candidate").read_text() == "fresh image candidate"
    assert (parent / "sibling-report").read_text() == "receiver sibling must survive"
    assert not workspace.with_name(workspace.name + ".before-checkpoint").exists()


def test_parent_capture_tamper_refuses_before_receiver_mutation(parent_workspace_point):
    start, workspace, _ = parent_workspace_point
    candidate = start.root / "components/workspace/nested/task/candidate"
    candidate.chmod(0o600)
    candidate.write_text("tampered")
    with pytest.raises(EffectRuntimeConflict, match="artifact members differ"):
        start.verify(str(workspace))
    with pytest.raises(EffectRuntimeConflict, match="artifact members differ"):
        warm.restore_workspace({"root": str(start.root), "sha256": start.sha256,
                                "deadline_epoch": start.deadline_epoch}, workspace)
    assert (workspace / "candidate").read_text() == "fresh image candidate"
