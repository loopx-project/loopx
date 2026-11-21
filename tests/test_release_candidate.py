from __future__ import annotations

import os
from pathlib import Path

import pytest

from loopx.release_candidate import (
    REPRESENTATIVE_DISTRIBUTION_PATHS,
    collect_deep_install_checks,
    collect_python_distribution_checks,
)


class _RecordedDistributionPath:
    def __init__(self, record_path: str, installed_path: Path) -> None:
        self.record_path = record_path
        self.installed_path = installed_path

    def __str__(self) -> str:
        return self.record_path

    def locate(self) -> Path:
        return self.installed_path


class _Distribution:
    def __init__(self, command_path: Path) -> None:
        self.files = [
            _RecordedDistributionPath("../../../bin/loopx", command_path),
        ]


def _materialize_distribution_paths(package_root: Path) -> None:
    for relative_path in REPRESENTATIVE_DISTRIBUTION_PATHS:
        path = package_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


@pytest.mark.parametrize("use_alias", [False, True])
def test_python_distribution_checks_accept_recorded_console_script_without_source_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_alias: bool,
) -> None:
    package_root = tmp_path / "site-packages"
    command_path = tmp_path / "bin" / "loopx"
    command_path.parent.mkdir(parents=True)
    command_path.touch()
    _materialize_distribution_paths(package_root)
    monkeypatch.setattr(
        "loopx.release_candidate.distribution",
        lambda _name: _Distribution(command_path),
    )
    monkeypatch.setattr(
        "loopx.release_candidate._import_summary",
        lambda: {"ok": True, "results": {}, "failed": []},
    )
    monkeypatch.setattr(
        "loopx.release_candidate._command_summary",
        lambda _path: {"ok": True, "results": {}, "failed": []},
    )

    invocation = command_path
    if use_alias:
        invocation = tmp_path / "loopx"
        try:
            invocation.symlink_to(command_path)
        except OSError:
            if os.name != "nt":
                raise
            pytest.skip("console script symlinks unavailable")

    payload = collect_python_distribution_checks(
        command_path=invocation,
        package_root=package_root,
    )

    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is True
    assert checks["command_package_same_distribution"]["ok"] is True
    assert checks["representative_distribution_paths"]["ok"] is True
    assert "scripts/loopx" not in payload["representative_cli"]["package_paths"][
        "required"
    ]


def test_deep_distribution_checks_prefer_current_invocation_over_path_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "site-packages"
    invocation_path = tmp_path / "current" / "bin" / "loopx"
    path_command = tmp_path / "old" / "bin" / "loopx"
    for command_path in (invocation_path, path_command):
        command_path.parent.mkdir(parents=True)
        command_path.touch()
    _materialize_distribution_paths(package_root)
    monkeypatch.setattr(
        "loopx.release_candidate.distribution",
        lambda _name: _Distribution(invocation_path),
    )
    monkeypatch.setattr(
        "loopx.release_candidate._import_summary",
        lambda: {"ok": True, "results": {}, "failed": []},
    )
    probed_commands: list[Path | None] = []
    monkeypatch.setattr(
        "loopx.release_candidate._command_summary",
        lambda command_path: (
            probed_commands.append(command_path)
            or {"ok": True, "results": {}, "failed": []}
        ),
    )

    payload = collect_deep_install_checks(
        command_path=path_command,
        invocation_path=invocation_path,
        package_root=package_root,
        invocation_root=None,
        distribution_root=package_root,
    )

    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is True
    assert checks["command_package_same_distribution"]["ok"] is True
    assert payload["distribution_command"]["command"] == str(invocation_path)
    assert probed_commands == [invocation_path]


@pytest.mark.parametrize("redirected_record", [False, True])
def test_python_distribution_checks_reject_command_from_another_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    redirected_record: bool,
) -> None:
    package_root = tmp_path / "site-packages"
    recorded_command = tmp_path / "installed" / "bin" / "loopx"
    active_command = tmp_path / "other" / "bin" / "loopx"
    active_command.parent.mkdir(parents=True)
    active_command.touch()
    recorded_command.parent.mkdir(parents=True)
    if redirected_record:
        try:
            recorded_command.symlink_to(active_command)
        except OSError:
            if os.name != "nt":
                raise
            pytest.skip("console script symlinks unavailable")
    else:
        recorded_command.touch()
    _materialize_distribution_paths(package_root)
    monkeypatch.setattr(
        "loopx.release_candidate.distribution",
        lambda _name: _Distribution(recorded_command),
    )
    monkeypatch.setattr(
        "loopx.release_candidate._import_summary",
        lambda: {"ok": True, "results": {}, "failed": []},
    )
    probed_commands: list[Path | None] = []

    def probe(command_path: Path | None) -> dict:
        probed_commands.append(command_path)
        return {"ok": command_path is not None, "results": {}, "failed": []}

    monkeypatch.setattr("loopx.release_candidate._command_summary", probe)

    payload = collect_python_distribution_checks(
        command_path=active_command,
        package_root=package_root,
    )

    checks = {check["id"]: check for check in payload["checks"]}
    assert payload["ok"] is False
    assert checks["command_package_same_distribution"]["ok"] is False
    assert probed_commands == [None]


def test_command_probes_overlap_but_keep_each_failure(monkeypatch):
    import subprocess
    import threading
    from loopx.release_candidate import _command_summary

    rendezvous = threading.Barrier(4)

    def run(argv, **kwargs):
        rendezvous.wait(timeout=5)
        if "commands" in argv:
            return subprocess.CompletedProcess(argv, 3)
        if "quota" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("loopx.release_candidate.subprocess.run", run)
    result = _command_summary(Path("/fixture/loopx"))
    assert not result["ok"]
    assert result["failed"] == ["commands", "quota_help"]
    assert list(result["results"]) == ["version", "commands", "status_help", "quota_help"]
    assert result["results"]["commands"]["returncode"] == 3
    assert "TimeoutExpired" in result["results"]["quota_help"]["detail"]
