from __future__ import annotations

import os
import shutil

import pytest

from loopx.codex_cli_runtime_probe import run_codex_cli_session_probe


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher discovery")
@pytest.mark.parametrize("suffix", [".cmd", ".ps1"])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_probe_runs_windows_launcher(tmp_path, monkeypatch, suffix, explicit_path):
    if suffix == ".ps1" and shutil.which("pwsh") is None:
        pytest.skip("PowerShell 7 is not installed")
    directory = tmp_path / "launcher with spaces"
    directory.mkdir()
    launcher = directory / f"codex{suffix}"
    if suffix == ".cmd":
        script = (
            "@echo off\n"
            'echo %*>> "%~dp0calls.txt"\n'
            "echo Usage: codex [OPTIONS] [PROMPT]\n"
            "echo Commands: exec resume\n"
        )
    else:
        script = (
            '$args -join " " | Add-Content -LiteralPath '
            '(Join-Path $PSScriptRoot "calls.txt")\n'
            "Write-Output 'Usage: codex [OPTIONS] [PROMPT]'\n"
            "Write-Output 'Commands: exec resume'\n"
        )
    launcher.write_text(script, encoding="utf-8")
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    requested = str(launcher) if explicit_path else "codex"

    payload = run_codex_cli_session_probe(codex_bin=requested, timeout_seconds=10)

    assert payload["codex_cli_available"] is True
    assert payload["command_errors"] == {}
    assert payload["codex_bin"] == requested
    assert payload["source"] == "real_help"
    assert (directory / "calls.txt").read_text(encoding="utf-8").splitlines() == [
        "--help", "exec --help", "resume --help",
    ]


def test_probe_reports_missing_command(tmp_path):
    payload = run_codex_cli_session_probe(codex_bin=str(tmp_path / "missing-codex"))

    assert payload["codex_cli_available"] is False
    assert payload["command_errors"] == {"root": "codex_cli_not_found"}
