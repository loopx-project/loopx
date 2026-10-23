from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from loopx.command_invocation import command_argv, resolve_command_path


def test_loopx_route_is_one_argument_and_does_not_leak_on_failure():
    from loopx.command_invocation import loopx_cli_argv, loopx_cli_route

    assert loopx_cli_argv() == ["loopx"]
    with loopx_cli_route("candidate's CLI"):
        assert loopx_cli_argv() == ["candidate's CLI", "--cli-bin", "candidate's CLI"]
        with pytest.raises(RuntimeError), loopx_cli_route("another-cli"):
            assert loopx_cli_argv()[0] == "another-cli"
            raise RuntimeError("failed invocation")
        assert loopx_cli_argv()[0] == "candidate's CLI"
    assert loopx_cli_argv() == ["loopx"]


@pytest.mark.parametrize("value", ["", " ", "-invalid", "name\nnext", "name\x00next"])
def test_invalid_cli_route_is_rejected_before_dispatch(value):
    from loopx.cli_runtime import build_cli_parser

    parser, sub = build_cli_parser()
    sub.add_parser("example")
    with pytest.raises(SystemExit) as failure:
        parser.parse_args(["--cli-bin", value, "example"])
    assert failure.value.code == 2


@pytest.mark.parametrize("module_name", ["loopx.cli", "loopx.cli_runtime"])
def test_full_and_selected_dispatch_reset_their_program_route(tmp_path, monkeypatch, module_name):
    import importlib
    from loopx import cli, cli_runtime
    from loopx.command_invocation import loopx_cli_argv

    observed = []

    def dispatch(_args, **_kwargs):
        observed.append(loopx_cli_argv())
        return 0

    monkeypatch.setattr(cli, "dispatch_common_command", dispatch)
    monkeypatch.setattr(cli_runtime, "dispatch_common_command", dispatch)
    entry = importlib.import_module(module_name)
    route = ["--registry", str(tmp_path / "unused.json")]
    assert entry.main([*route, "--cli-bin", "candidate-cli", "doctor"]) == 0
    assert entry.main([*route, "doctor"]) == 0
    assert observed == [["candidate-cli", "--cli-bin", "candidate-cli"], ["loopx"]]
    assert loopx_cli_argv() == ["loopx"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell script discovery is Windows-specific")
def test_resolve_and_run_powershell_script_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 is not installed")
    script = tmp_path / "loopx.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        'Write-Output ($args -join "|")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    resolved = resolve_command_path("loopx")

    assert resolved == script
    argv = command_argv(resolved, ["alpha", "two words"], pwsh=pwsh)
    result = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "alpha|two words"
