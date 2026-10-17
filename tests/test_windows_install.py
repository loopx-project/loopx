from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import venv
from pathlib import Path
from typing import Any

import pytest

from loopx import windows_install
from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS


def _run_loopx(
    *,
    pwsh: str,
    launcher: Path,
    args: list[str],
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-File", str(launcher), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )


def _windows_user_path(pwsh: str) -> str:
    result = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            (
                "$value = [Environment]::GetEnvironmentVariable('Path', 'User'); "
                "if ($null -eq $value) { 'null' } else { "
                "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value)) }"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return result.stdout.strip()


def _restore_windows_user_path(pwsh: str, encoded_path: str) -> None:
    env = dict(os.environ)
    env["LOOPX_TEST_USER_PATH"] = encoded_path
    subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            (
                "$encoded = $env:LOOPX_TEST_USER_PATH; "
                "$value = if ($encoded -eq 'null') { $null } else { "
                "[Text.Encoding]::UTF8.GetString("
                "[Convert]::FromBase64String($encoded)) }; "
                "[Environment]::SetEnvironmentVariable('Path', $value, 'User')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )


@pytest.mark.parametrize("directory", ["python 中文", "python 中文 🚀"])
def test_resolve_python_preserves_unicode_interpreter_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    runtime = tmp_path / directory
    venv.EnvBuilder(with_pip=False).create(runtime)
    interpreter = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")
    monkeypatch.setenv("PYTHONUTF8", "0")

    assert windows_install._resolve_python(str(interpreter)) == interpreter.resolve()


def test_windows_candidate_failure_reports_required_doctor_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "checks": [
            {
                "id": "typescript_control_plane_ready",
                "required": True,
                "ok": False,
                "detail": "runtime unavailable",
                "recommended_action": "Install the supported Node.js runtime.",
            }
        ],
        "release_candidate": {
            "checks": [
                {
                    "id": "representative_cli_commands",
                    "required": True,
                    "ok": True,
                }
            ]
        },
        "diagnostics": "x" * 2500,
    }
    monkeypatch.setattr(
        windows_install.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout=json.dumps(payload), stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match=r"doctor\.typescript_control_plane_ready") as exc_info:
        windows_install._validate_candidate(
            tmp_path / "release", python=Path(sys.executable), skills_dir=tmp_path / "skills"
        )

    assert "detail=runtime unavailable" in str(exc_info.value)
    assert "recommended_action=Install the supported Node.js runtime." in str(
        exc_info.value
    )
    assert "diagnostics" not in str(exc_info.value)


def test_windows_candidate_failure_preserves_structured_error_without_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "ok": False,
        "error": "selected Node.js runtime is unsupported",
        "recommended_action": "Install Node.js 22 or newer.",
    }
    monkeypatch.setattr(
        windows_install.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout=json.dumps(payload), stderr=""
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        windows_install._validate_candidate(
            tmp_path / "release", python=Path(sys.executable), skills_dir=tmp_path / "skills"
        )

    assert "selected Node.js runtime is unsupported" in str(exc_info.value)
    assert "Install Node.js 22 or newer." in str(exc_info.value)
    assert "no failed required checks reported" not in str(exc_info.value)


def test_windows_candidate_failure_keeps_all_ids_and_late_node_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = [
        {
            "id": check_id,
            "required": True,
            "ok": False,
            "detail": f"{check_id} detail " + ("x" * 380),
            "recommended_action": f"Repair {check_id} " + ("y" * 380),
        }
        for check_id in (
            "command_package_same_root",
            "representative_cli_commands",
            "representative_cli_imports",
            "typescript_effect_runtime_ready",
        )
    ]
    monkeypatch.setattr(
        windows_install.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout=json.dumps({"checks": failures}), stderr=""
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        windows_install._validate_candidate(
            tmp_path / "release", python=Path(sys.executable), skills_dir=tmp_path / "skills"
        )

    message = str(exc_info.value)
    for check_id in (
        "command_package_same_root",
        "representative_cli_commands",
        "representative_cli_imports",
        "typescript_effect_runtime_ready",
    ):
        assert f"doctor.{check_id}" in message
    assert "typescript_effect_runtime_ready detail=" in message
    assert "recommended_action=Repair typescript_effect_runtime_ready" in message


def test_doctor_failure_summary_marks_ids_that_exceed_its_budget() -> None:
    summary = windows_install._doctor_failure_summary(
        json.dumps(
            {
                "checks": [{"id": "x" * 2100, "required": True, "ok": False}],
            }
        )
    )

    assert summary is not None
    assert len(summary) <= 2000
    assert "1 required failed check IDs omitted by the summary limit" in summary


def test_chat_bundle_preflight_preserves_stdout_with_legacy_pointer(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    bundle_builder = source_root / "bundle_builder.py"
    bundle_builder.write_text("print('bundle progress')\n", encoding="utf-8")
    pointer = tmp_path / "current-release.json"
    pointer.write_text('{"release_id":"known-good"}\n', encoding="utf-8")

    windows_install._ensure_chat_bundle(
        bundle_builder=bundle_builder,
        source_root=source_root,
        python=Path(sys.executable),
        pointer=pointer,
    )

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == ["bundle progress"]


def test_authority_upgrade_runs_from_candidate_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "releases" / "candidate"
    skills_dir = tmp_path / "codex" / "skills"
    observed_command: list[str] = []
    observed_env: dict[str, str] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed_command.extend(command)
        observed_env.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_install.subprocess, "run", run)

    windows_install._upgrade_authority_archive(
        release_root,
        python=Path(sys.executable),
        skills_dir=skills_dir,
    )

    assert observed_command == [
        sys.executable,
        "-I",
        str(release_root / "scripts" / "loopx_entry.py"),
        "--format",
        "json",
        "authority-archive",
        "upgrade",
        "--all-known",
        "--execute",
    ]
    assert observed_env["LOOPX_RELEASE_ROOT"] == str(release_root)
    assert observed_env["CODEX_HOME"] == str(skills_dir.parent)
    assert observed_env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_authority_upgrade_failure_reports_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="loopx runtime error: LOOPX_RELEASE_ROOT is not set",
        )

    monkeypatch.setattr(windows_install.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="LOOPX_RELEASE_ROOT is not set"):
        windows_install._upgrade_authority_archive(
            tmp_path / "release",
            python=Path(sys.executable),
            skills_dir=tmp_path / "codex" / "skills",
        )


@pytest.mark.parametrize("previously_installed", [False, True])
def test_skill_rollback_keeps_interrupted_directory_after_snapshot_cleanup(
    tmp_path: Path, previously_installed: bool
) -> None:
    skill = tmp_path / "skills" / "browser-skill"
    skill.parent.mkdir()
    if previously_installed:
        skill.mkdir()
        (skill / "note.txt").write_text("Previous installed version\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([skill], Path(temporary))
        skill.mkdir(exist_ok=True)
        (skill / "note.txt").write_text("Customization made during installation\n")
        retained = windows_install._restore_paths(snapshots)

    if previously_installed:
        assert (skill / "note.txt").read_text() == "Previous installed version\n"
    else:
        assert not skill.exists()
    assert any(
        path.read_text() == "Customization made during installation\n"
        for path in tmp_path.rglob("note.txt")
    ), "Rolling back must not delete the only copy of a concurrent customization"
    assert len(retained) == 1
    assert retained[0].parent.parent == skill.parent
    assert (
        retained[0] / "note.txt"
    ).read_text() == "Customization made during installation\n"


@pytest.mark.parametrize("relative_link", [False, True])
def test_skill_rollback_keeps_replacement_symlink_without_mutating_its_target(
    tmp_path: Path,
    relative_link: bool,
) -> None:
    previous = tmp_path / "previous"
    custom = tmp_path / "custom"
    for target in (previous, custom):
        target.mkdir()
        (target / "note.txt").write_text(target.name)
    skill = tmp_path / "browser-skill"
    try:
        skill.symlink_to(
            previous.name if relative_link else previous, target_is_directory=True
        )
    except OSError:
        pytest.skip("The host does not permit directory symlinks")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([skill], Path(temporary))
        skill.unlink()
        skill.symlink_to(
            custom.name if relative_link else custom, target_is_directory=True
        )
        interrupted_target = os.readlink(skill)
        retained = windows_install._restore_paths(snapshots)

    assert skill.is_symlink() and skill.resolve() == previous
    assert any(
        path.is_symlink() and os.readlink(path) == interrupted_target
        for path in tmp_path.rglob("*")
    ), "The replacement link must remain recoverable after rollback"
    assert len(retained) == 1 and retained[0].is_symlink()
    assert os.readlink(retained[0]) == interrupted_target
    # Archived relative links retain their literal target; returning the link
    # to its original location restores that target's meaning without editing it.
    skill.unlink()
    retained[0].replace(skill)
    assert skill.resolve() == custom
    assert (previous / "note.txt").read_text() == "previous"
    assert (custom / "note.txt").read_text() == "custom"


def test_failed_rollback_keeps_interrupted_skill_outside_temporary_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "browser-skill"
    skill.mkdir()
    (skill / "note.txt").write_text("Previous installed version\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([skill], Path(temporary))
        (skill / "note.txt").write_text("Customization made during installation\n")

        def fail_copy(source: Path, target: Path) -> None:
            raise OSError("Injected restore failure")

        monkeypatch.setattr(windows_install, "_copy_path", fail_copy)
        with pytest.raises(Exception, match="Injected restore failure") as caught:
            windows_install._restore_paths(snapshots)

    retained = [
        path
        for path in tmp_path.rglob("note.txt")
        if path.read_text() == "Customization made during installation\n"
    ]
    assert len(retained) == 1, "A restore failure must retain the interrupted skill"
    assert str(retained[0].parent) in str(caught.value)


@pytest.mark.parametrize("previously_installed", [False, True])
def test_declared_skill_path_keeps_regular_file_replacement(
    tmp_path: Path,
    previously_installed: bool,
) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    skill = skills / PACKAGED_HOST_SKILL_IDS[0]
    skill_paths = windows_install._skill_mutation_paths(skills)
    assert skill in skill_paths
    if previously_installed:
        skill.mkdir()
        (skill / "SKILL.md").write_text("Previous installed version\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([skill], Path(temporary))
        if skill.exists():
            shutil.rmtree(skill)
        skill.write_text("Concurrent replacement file\n")
        retained = windows_install._restore_paths(snapshots, skill_paths=skill_paths)

    if previously_installed:
        assert (skill / "SKILL.md").read_text() == "Previous installed version\n"
    else:
        assert not skill.exists()
    assert len(retained) == 1
    assert retained[0].read_text() == "Concurrent replacement file\n"


@pytest.mark.parametrize(
    ("previously_installed", "parent_exists"),
    [(False, False), (False, True), (True, True)],
)
def test_missing_skill_restores_snapshot_and_remaining_paths(
    tmp_path: Path, previously_installed: bool, parent_exists: bool
) -> None:
    skill = tmp_path / "skills" / "browser-skill"
    pointer = tmp_path / "current-release.json"
    pointer.write_bytes(b"previous pointer\n")
    if parent_exists:
        skill.parent.mkdir()
    if previously_installed:
        skill.mkdir()
        (skill / "SKILL.md").write_bytes(b"previous skill\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        # Recovery visits the missing skill before the pointer.
        snapshots = windows_install._snapshot_paths([pointer, skill], Path(temporary))
        pointer.write_bytes(b"interrupted pointer\n")
        if previously_installed:
            shutil.rmtree(skill)
        retained = windows_install._restore_paths(snapshots, skill_paths=[skill])

    assert pointer.read_bytes() == b"previous pointer\n"
    if previously_installed:
        assert (skill / "SKILL.md").read_bytes() == b"previous skill\n"
    else:
        assert not skill.exists()
    assert retained == [], "An absent target has no interrupted content to retain"
    assert not list(tmp_path.rglob("*.install-recovery.*"))


def test_skill_rollback_retains_dangling_replacement_link(tmp_path: Path) -> None:
    skill = tmp_path / "browser-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"previous skill\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([skill], Path(temporary))
        shutil.rmtree(skill)
        try:
            skill.symlink_to("missing-customization", target_is_directory=True)
        except OSError:
            pytest.skip("The host does not permit directory symlinks")
        retained = windows_install._restore_paths(snapshots, skill_paths=[skill])

    assert (skill / "SKILL.md").read_bytes() == b"previous skill\n"
    assert len(retained) == 1 and retained[0].is_symlink()
    assert os.readlink(retained[0]) == "missing-customization"


@pytest.mark.parametrize("previously_installed", [False, True])
def test_windows_installer_restores_after_skill_copy_fails_before_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previously_installed: bool,
) -> None:
    source = tmp_path / "source"
    install_root = tmp_path / "install"
    skills = tmp_path / "codex" / "skills"
    skill = skills / PACKAGED_HOST_SKILL_IDS[0]
    candidate = install_root / "releases" / "copy-failure"
    source_skill = source / "skills" / skill.name / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_bytes(b"candidate skill\n")
    launcher_source = source / "scripts" / "loopx.ps1"
    launcher_source.parent.mkdir()
    launcher_source.write_bytes(b"candidate launcher\n")
    previous = {
        install_root / "current-release.json": b"previous pointer\n",
        tmp_path / "bin" / "loopx.ps1": b"previous launcher\n",
        tmp_path / "bin" / windows_install.LAUNCHER_POINTER_FILENAME: b"previous sidecar\n",
    }
    if previously_installed:
        previous[skill / "SKILL.md"] = b"previous skill\n"
    for path, content in previous.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    # Exercise the installer filesystem transaction on every host. Native
    # Windows/PowerShell admission and candidate qualification remain separate.
    if os.name != "nt":
        monkeypatch.setattr(
            windows_install, "os",
            SimpleNamespace(name="nt", environ=os.environ, replace=os.replace, getpid=os.getpid),
        )
    monkeypatch.setattr(windows_install, "_validate_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(windows_install, "_upgrade_authority_archive", lambda *args, **kwargs: None)
    copytree = shutil.copytree
    attempted: list[Path] = []

    def fail_skill_copy(source_path: Any, target: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(target) == skill and Path(source_path) == candidate / "skills" / skill.name:
            assert not skill.exists(), "The real installer already removed the old skill"
            attempted.append(Path(target))
            raise OSError("Injected skill copy failure before target creation")
        return copytree(source_path, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", fail_skill_copy)
    with pytest.raises(Exception) as caught:
        windows_install.install_windows(
            source_root=source,
            install_root=install_root,
            bin_dir=tmp_path / "bin",
            skills_dir=skills,
            python_requested=sys.executable,
            install_skills=True,
            requested_release_id="copy-failure",
        )

    assert attempted == [skill]
    assert not list(install_root.glob(".install-rollback.*"))
    for path, content in previous.items():
        assert path.read_bytes() == content, "Rollback must survive temporary snapshot cleanup"
    if not previously_installed:
        assert not skill.exists()
    assert (candidate / "skills" / skill.name / "SKILL.md").read_bytes() == b"candidate skill\n"
    assert isinstance(caught.value, OSError), "Successful rollback preserves the original error"
    assert "Injected skill copy failure" in str(caught.value)


def test_regular_file_rollback_retains_existing_launcher_behavior(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "loopx.ps1"
    launcher.write_text("Previous launcher\n")
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        snapshots = windows_install._snapshot_paths([launcher], Path(temporary))
        launcher.write_text("Interrupted candidate launcher\n")
        windows_install._restore_paths(snapshots)
    assert launcher.read_text() == "Previous launcher\n"
    assert sorted(tmp_path.iterdir()) == [launcher]


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer regression")
def test_windows_installer_promotes_release_and_runs_doctor(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 is not installed")
    repo_root = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "share" / "loopx"
    bin_dir = tmp_path / "bin"
    skills_dir = tmp_path / "codex" / "skills"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    original_user_path = _windows_user_path(pwsh)
    try:
        install = subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(repo_root / "scripts" / "install-windows.ps1"),
                "-Python",
                sys.executable,
                "-InstallRoot",
                str(install_root),
                "-BinDir",
                str(bin_dir),
                "-SkillsDir",
                str(skills_dir),
                "-AddToUserPath",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=180,
        )
    finally:
        _restore_windows_user_path(pwsh, original_user_path)

    assert install.returncode == 0, install.stderr
    installed = json.loads(install.stdout)
    assert f"LoopX Windows user PATH includes: {bin_dir}" in install.stderr
    pointer_path = install_root / "current-release.json"
    assert installed["pointer"] == str(pointer_path)
    assert (bin_dir / "loopx.ps1").is_file()
    launcher_pointer = bin_dir / windows_install.LAUNCHER_POINTER_FILENAME
    assert installed["launcher_pointer"] == str(launcher_pointer)
    assert launcher_pointer.is_file()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert json.loads(launcher_pointer.read_text(encoding="utf-8")) == pointer
    release_root = Path(pointer["release_root"])
    assert release_root.is_dir()
    expected_skills = {
        "loopx",
        *PACKAGED_HOST_SKILL_IDS,
        "loopx-deepresearch",
        "loopx-global-summary",
        "loopx-global-gates",
        "loopx-global-todos",
        "loopx-global-risks",
    }
    assert expected_skills == {
        path.name for path in skills_dir.iterdir() if (path / "SKILL.md").is_file()
    }

    # A real fresh shell does not inherit the installer's pointer override. The
    # launcher must discover the sidecar next to itself, including when the
    # caller selected a non-default InstallRoot.
    launch_env = dict(env)
    launch_env.pop("LOOPX_CURRENT_RELEASE_FILE", None)
    launcher = bin_dir / "loopx.ps1"
    doctor = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            "--format",
            "json",
            "doctor",
            "--deep",
        ],
        env=launch_env,
    )

    assert doctor.returncode == 0, doctor.stderr
    payload = json.loads(doctor.stdout)
    assert payload["release_candidate"]["ok"] is True
    assert payload["release_provenance"]["default_release"]["root"] == str(release_root)

    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx" / "registry.json"
    runtime = tmp_path / "runtime"
    common = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
    ]
    bootstrap = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            *common,
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            "windows-probe",
            "--objective",
            "Verify the native Windows PowerShell lifecycle",
        ],
        env=launch_env,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr

    registered = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            *common, "register-agent", "--goal-id", "windows-probe",
            "--agent-id", "windows-worker", "--require-new", "--execute",
        ],
        env=launch_env,
    )
    assert registered.returncode == 0, registered.stderr or registered.stdout

    todo = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            *common,
            "todo",
            "add",
            "--goal-id",
            "windows-probe",
            "--role",
            "agent",
            "--text",
            "[P0] Validate Windows PowerShell state writeback",
            "--task-class",
            "advancement_task",
            "--claimed-by",
            "windows-worker",
            "--validation-command-json",
            json.dumps(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('result.txt').read_text(encoding='utf-8') == '验证完成 🚀'",
                ]
            ),
            "--validation-label",
            "Windows artifact contents",
        ],
        env=launch_env,
    )
    assert todo.returncode == 0, todo.stderr
    state_file = project / ".loopx" / "goals" / "windows-probe" / "ACTIVE_GOAL_STATE.md"
    assert "[P0] Validate Windows PowerShell state writeback" in state_file.read_text(
        encoding="utf-8"
    )
    assert not (project / ".codex" / "goals" / "windows-probe").exists()

    quota = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            *common,
            "quota",
            "should-run",
            "--goal-id",
            "windows-probe",
            "--agent-id",
            "windows-worker",
            "--verbose",
        ],
        env=launch_env,
    )
    assert quota.returncode == 0, quota.stderr or quota.stdout
    quota_payload = json.loads(quota.stdout)
    assert quota_payload["should_run"] is True

    todo_id = json.loads(todo.stdout)["todo_id"]

    acquired = _run_loopx(
        pwsh=pwsh,
        launcher=launcher,
        args=[
            *common, "task-lease", "acquire", "--goal-id", "windows-probe",
            "--todo-id", todo_id, "--owner", "windows-worker",
            "--idempotency-key", "windows-writeback",
        ],
        env=launch_env,
    )
    assert acquired.returncode == 0, acquired.stderr or acquired.stdout

    def todo_command(action: str, *args: str) -> subprocess.CompletedProcess[str]:
        # Each invocation starts a fresh PowerShell process through the installed
        # launcher, without the installer's release-pointer override.
        return _run_loopx(
            pwsh=pwsh,
            launcher=launcher,
            args=[*common, "todo", action, "--goal-id", "windows-probe", *args],
            env=launch_env,
        )

    completion_args = [
        "--todo-id", todo_id,
        "--agent-id", "windows-worker",
        "--task-lease-idempotency-key", "windows-writeback",
        "--task-lease-expected-version", str(json.loads(acquired.stdout)["lease"]["version"]),
        "--evidence", "Synthetic Unicode artifact validated on Windows.",
        "--no-follow-up",
    ]
    rejected = todo_command("complete", *completion_args)
    assert rejected.returncode == 1, rejected.stderr or rejected.stdout
    assert json.loads(rejected.stdout)["validation_blocked_completion"] is True

    pending = todo_command("list", "--todo-id", todo_id)
    assert pending.returncode == 0, pending.stderr or pending.stdout
    assert json.loads(pending.stdout)["todo"]["status"] == "open"

    (project / "result.txt").write_text("验证完成 🚀", encoding="utf-8")
    completed = todo_command("complete", *completion_args)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    completion = json.loads(completed.stdout)
    assert completion["completed"] is True
    receipt_id = completion["completion_receipt_id"]
    assert receipt_id

    recovered = todo_command("list", "--todo-id", todo_id)
    assert recovered.returncode == 0, recovered.stderr or recovered.stdout
    recovered_todo = json.loads(recovered.stdout)["todo"]
    assert recovered_todo["status"] == "done"
    assert recovered_todo["completion_receipt_id"] == receipt_id

    replayed = todo_command("complete", *completion_args)
    assert replayed.returncode == 0, replayed.stderr or replayed.stdout
    replay = json.loads(replayed.stdout)
    assert replay["completion_receipt_id"] == receipt_id
    assert replay["changed"] is False


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer regression")
def test_windows_installer_preserves_externally_managed_skills(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 is not installed")
    repo_root = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "share" / "loopx"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    codex_skills = home / ".codex" / "skills"
    agents_skills = home / ".agents" / "skills"
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        shutil.copytree(repo_root / "skills" / skill_id, agents_skills / skill_id)

    env = dict(os.environ)
    env.update(
        {
            "CODEX_HOME": str(home / ".codex"),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    install = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(repo_root / "scripts" / "install-windows.ps1"),
            "-Python",
            sys.executable,
            "-InstallRoot",
            str(install_root),
            "-BinDir",
            str(bin_dir),
            "-SkillsDir",
            str(codex_skills),
            "-SkipSkills",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )

    assert install.returncode == 0, install.stderr
    installed = json.loads(install.stdout)
    assert installed["installed_skill_ids"] == []
    assert not codex_skills.exists()

    launch_env = dict(env)
    launch_env["LOOPX_CURRENT_RELEASE_FILE"] = installed["pointer"]
    doctor = _run_loopx(
        pwsh=pwsh,
        launcher=bin_dir / "loopx.ps1",
        args=["--format", "json", "doctor", "--deep"],
        env=launch_env,
    )

    assert doctor.returncode == 0, doctor.stderr
    payload = json.loads(doctor.stdout)
    assert payload["skill_delivery"]["status"] == "ready"
    assert payload["skill_delivery"]["owner"] == "external_skill_manager"
    assert payload["install_freshness"]["externally_managed_skills"] is True
    assert "-SkipSkills" in payload["install_freshness"]["upgrade_command"]
    assert payload["release_candidate"]["ok"] is True
    assert all(
        Path(skill["path"]).is_relative_to(agents_skills)
        for skill in payload["skills"].values()
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer regression")
def test_windows_installer_keeps_pointer_when_candidate_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_root = tmp_path / "share" / "loopx"
    pointer = install_root / "current-release.json"
    pointer.parent.mkdir(parents=True)
    original_pointer = '{"release_id":"known-good"}\n'
    pointer.write_text(original_pointer, encoding="utf-8")

    monkeypatch.setattr(windows_install, "_copy_release", lambda source, target: None)
    monkeypatch.setattr(windows_install, "write_release_manifest", lambda **kwargs: None)

    def reject_candidate(*args: object, **kwargs: object) -> None:
        raise RuntimeError("candidate rejected")

    monkeypatch.setattr(windows_install, "_validate_candidate", reject_candidate)

    with pytest.raises(RuntimeError, match="candidate rejected"):
        windows_install.install_windows(
            source_root=tmp_path / "source",
            install_root=install_root,
            bin_dir=tmp_path / "bin",
            skills_dir=tmp_path / "codex" / "skills",
            python_requested=sys.executable,
            install_skills=False,
            requested_release_id="rejected",
        )

    assert pointer.read_text(encoding="utf-8") == original_pointer
    assert not (install_root / "releases" / "rejected").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer regression")
def test_windows_installer_rolls_back_user_surfaces_and_retains_upgraded_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "share" / "loopx"
    bin_dir = tmp_path / "bin"
    skills_dir = tmp_path / "codex" / "skills"
    pointer = install_root / "current-release.json"
    launcher = bin_dir / "loopx.ps1"
    launcher_pointer = bin_dir / windows_install.LAUNCHER_POINTER_FILENAME
    existing_skill = skills_dir / PACKAGED_HOST_SKILL_IDS[0] / "SKILL.md"
    for path, content in (
        (pointer, '{"release_id":"known-good"}\n'),
        (launcher, "# known-good launcher\n"),
        (launcher_pointer, '{"release_id":"known-good"}\n'),
        (existing_skill, "# known-good skill\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def fail_after_skill_write(
        release_root: Path, target_skills_dir: Path, installed_at: str
    ) -> list[str]:
        del release_root, installed_at
        existing_skill_path = target_skills_dir / PACKAGED_HOST_SKILL_IDS[0] / "SKILL.md"
        existing_skill_path.write_text("# partial update\n", encoding="utf-8")
        raise RuntimeError("late skill failure")

    monkeypatch.setattr(windows_install, "_install_skills", fail_after_skill_write)

    with pytest.raises(RuntimeError, match="late skill failure"):
        windows_install.install_windows(
            source_root=repo_root,
            install_root=install_root,
            bin_dir=bin_dir,
            skills_dir=skills_dir,
            python_requested=sys.executable,
            install_skills=True,
            requested_release_id="rejected-late",
        )

    assert pointer.read_text(encoding="utf-8") == '{"release_id":"known-good"}\n'
    assert launcher.read_text(encoding="utf-8") == "# known-good launcher\n"
    assert launcher_pointer.read_text(encoding="utf-8") == (
        '{"release_id":"known-good"}\n'
    )
    assert existing_skill.read_text(encoding="utf-8") == "# known-good skill\n"
    retained_candidate = install_root / "releases" / "rejected-late"
    assert retained_candidate.is_dir()
    assert (retained_candidate / "scripts" / "loopx_entry.py").is_file()
