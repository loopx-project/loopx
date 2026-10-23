"""Install/repair guidance LoopX prints must run on the host that prints it.

The bootstrap/connect hint is the first repair instruction a new operator sees.
Native Windows has no ``python3`` launcher and no Bash, while ``loopx doctor``
already names the interpreter and snapshot installer this host owns. These
tests pin that platform rule together with the PowerShell invocation semantics
the printed Windows commands need, so the guidance is executable and not only
worded. They also pin the layout rule: the fallback names the snapshot
installer only while the checkout that owns this module ships that script, and
an installed distribution recovers through the interpreter's own pip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import install_contract
from loopx.bootstrap import bootstrap_project

POSIX_ONLY_TOKENS = ("python3 ", "| bash", "export PATH=")


PLAIN_WINDOWS_INTERPRETER = r"C:\Python311\python.exe"

WINDOWS_INTERPRETERS = (
    PLAIN_WINDOWS_INTERPRETER,
    r"C:\Program Files\Python311\python.exe",
    r"C:\O'Brien\python.exe",
)

PWSH = shutil.which("pwsh")


def powershell_literal(value: str) -> str:
    """PowerShell single-quoted literal, written independently of the owner."""

    return "'" + value.replace("'", "''") + "'"


def pretend_windows_host(
    monkeypatch: pytest.MonkeyPatch, interpreter: str | None = None
) -> None:
    """Exercise the Windows branch on any host without switching ``os.name``."""

    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: True)
    if interpreter is not None:
        monkeypatch.setattr(sys, "executable", interpreter)


def pretend_module_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Install the owner module under ``root`` the way a wheel layout would."""

    module_file = root / "loopx" / "install_contract.py"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("# fixture module owned by this layout\n", encoding="utf-8")
    monkeypatch.setattr(install_contract, "__file__", str(module_file))
    return module_file.resolve().parents[1]


def checkout_layout(root: Path) -> Path:
    """A LoopX checkout that ships the Windows snapshot installer."""

    installer = root / "scripts" / "install-windows.ps1"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text("# fixture snapshot installer\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "loopx"\n', encoding="utf-8"
    )
    return installer


def powershell_parse_errors(pwsh: str, script: Path) -> str:
    probe = (
        "$errors = $null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"{powershell_literal(str(script))}, [ref]$null, [ref]$errors) > $null;"
        "$errors | ForEach-Object { $_.Message }"
    )
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def bootstrap_hint(tmp_path: Path) -> tuple[str, str]:
    registry = tmp_path / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    payload = bootstrap_project(
        project=tmp_path,
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="install-hint-goal",
        objective="Print install guidance.",
        domain="test",
        role="controller",
        parent_goal_id=None,
        state_file=None,
        goal_doc=None,
        adapter_kind="generic_project_goal_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=0,
        allowed_domains=[],
        write_scope=[],
        force=False,
        dry_run=True,
        sync_global=False,
    )
    return str(payload["install_repair_command"]), str(
        payload["archive_fallback_install_command"]
    )


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_install_hint_commands_follow_the_host_platform(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: os_name == "nt")
    repair = install_contract.install_repair_command()
    fallback = install_contract.archive_fallback_install_command()

    if os_name == "nt":
        for command in (repair, fallback):
            for token in POSIX_ONLY_TOKENS:
                assert token not in command, command
        assert repair.splitlines()[0] == (
            f"& {powershell_literal(sys.executable)} -m pip install --upgrade loopx"
        )
        assert "loopx workflow-skills --install" in repair
        assert "loopx doctor" in repair
        checkout_installer = (
            Path(install_contract.__file__).resolve().parents[1]
            / "scripts"
            / "install-windows.ps1"
        )
        assert checkout_installer.is_file()
        assert powershell_literal(str(checkout_installer)) in fallback
        assert "curl" not in fallback
    else:
        assert repair == install_contract.DEFAULT_INSTALL_REPAIR_COMMAND
        assert fallback == install_contract.ARCHIVE_FALLBACK_INSTALL_COMMAND


@pytest.mark.parametrize("interpreter", WINDOWS_INTERPRETERS)
def test_windows_repair_command_invokes_the_interpreter_with_the_call_operator(
    monkeypatch: pytest.MonkeyPatch, interpreter: str
) -> None:
    pretend_windows_host(monkeypatch, interpreter)

    repair = install_contract.install_repair_command()

    assert repair.splitlines()[0] == (
        f"& {powershell_literal(interpreter)} -m pip install --upgrade loopx"
    )


def test_windows_fallback_prints_the_owned_checkout_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_layout(tmp_path / "checkout")
    pretend_windows_host(monkeypatch, r"C:\Python311\python.exe")
    root = pretend_module_root(monkeypatch, tmp_path / "checkout")

    fallback = install_contract.archive_fallback_install_command()

    installer = root / "scripts" / "install-windows.ps1"
    assert installer.is_file()
    assert fallback.splitlines() == [
        "pwsh -NoLogo -NoProfile -File "
        f"{powershell_literal(str(installer))} "
        f"-Python {powershell_literal(PLAIN_WINDOWS_INTERPRETER)}",
        "loopx doctor",
    ]


@pytest.mark.parametrize(
    "layout_files",
    [(), ("scripts/install-windows.ps1",), ("pyproject.toml",)],
    ids=["installed-distribution", "unowned-script", "checkout-without-installer"],
)
def test_windows_fallback_without_an_owned_installer_uses_the_interpreter_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, layout_files: tuple[str, ...]
) -> None:
    layout = tmp_path / "site-packages"
    for relative in layout_files:
        path = layout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# not the checkout that owns the module\n", encoding="utf-8")
    pretend_windows_host(monkeypatch, r"C:\Python311\python.exe")
    pretend_module_root(monkeypatch, layout)

    fallback = install_contract.archive_fallback_install_command()

    assert fallback.splitlines() == [
        f"& {powershell_literal(PLAIN_WINDOWS_INTERPRETER)} "
        "-m pip install --upgrade loopx",
        "loopx workflow-skills --install",
        "loopx doctor",
    ]


def test_windows_installed_fallback_keeps_doctor_options_and_skips_owned_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pretend_windows_host(monkeypatch, PLAIN_WINDOWS_INTERPRETER)
    pretend_module_root(monkeypatch, tmp_path / "site-packages")

    command = install_contract.no_clone_upgrade_command(
        doctor_agent_type="codex", skip_skills=True
    )

    assert command.splitlines() == [
        f"& {powershell_literal(PLAIN_WINDOWS_INTERPRETER)} "
        "-m pip install --upgrade loopx",
        f"loopx doctor --agent-type {powershell_literal('codex')}",
    ]


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_preflight_commands_follow_the_host_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, os_name: str
) -> None:
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: os_name == "nt")
    if os_name == "nt":
        monkeypatch.setattr(sys, "executable", PLAIN_WINDOWS_INTERPRETER)
    doctor = install_contract.doctor_preflight_command(cli_bin="loopx-canary")
    codex = install_contract.codex_cli_install_preflight(
        cli_bin="loopx-canary", doctor_agent_type="codex-cli"
    )

    if os_name == "nt":
        for command in (doctor, codex):
            for token in POSIX_ONLY_TOKENS:
                assert token not in command, command
            assert "Get-Command" in command
            assert "pwsh" not in command
            assert powershell_literal(PLAIN_WINDOWS_INTERPRETER) in command
        assert doctor.splitlines()[-1] == "loopx-canary doctor | Out-Null"
        assert "loopx-canary workflow-skills --install" in codex
        assert (
            f"loopx-canary doctor --agent-type {powershell_literal('codex-cli')} | Out-Null"
            in codex
        )
    else:
        assert doctor.startswith('export PATH="$HOME/.local/bin:$PATH"')
        assert "loopx-canary doctor >/dev/null" in doctor
        assert "python3 -m pip install --upgrade loopx" in codex
        assert "loopx-canary doctor --agent-type codex-cli >/dev/null" in codex


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to parse the printed commands")
def test_preflight_commands_parse_as_powershell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch, PLAIN_WINDOWS_INTERPRETER)
    commands = {
        "doctor": install_contract.doctor_preflight_command(),
        "codex": install_contract.codex_cli_install_preflight(doctor_agent_type="codex-cli"),
    }

    for name, command in commands.items():
        script = tmp_path / f"{name}-preflight.ps1"
        script.write_text(command, encoding="utf-8")

        errors = powershell_parse_errors(pwsh, script)

        assert errors == "", (name, errors)


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to parse the printed commands")
@pytest.mark.parametrize("layout", ["checkout", "installed"])
@pytest.mark.parametrize("interpreter", WINDOWS_INTERPRETERS)
def test_windows_commands_parse_as_powershell(
    monkeypatch: pytest.MonkeyPatch, interpreter: str, tmp_path: Path, layout: str
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch, interpreter)
    root = tmp_path / layout
    if layout == "checkout":
        checkout_layout(root)
    pretend_module_root(monkeypatch, root)
    commands = {
        "repair": install_contract.install_repair_command(),
        "fallback": install_contract.archive_fallback_install_command(),
    }

    for name, command in commands.items():
        script = tmp_path / f"{name}-{layout}-hint.ps1"
        script.write_text(command, encoding="utf-8")

        errors = powershell_parse_errors(pwsh, script)

        assert errors == "", (name, layout, errors)


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to run the printed command")
@pytest.mark.parametrize(
    "command_name", ["install_repair_command", "installed_fallback"]
)
def test_windows_interpreter_witness_runs_a_real_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command_name: str
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch)
    if command_name == "installed_fallback":
        pretend_module_root(monkeypatch, tmp_path / "site-packages")
        command = install_contract.archive_fallback_install_command()
    else:
        command = install_contract.install_repair_command()
    interpreter_line = command.splitlines()[0]
    witness = interpreter_line.replace(
        "-m pip install --upgrade loopx", "-c \"print('loopx-witness-ok')\""
    )
    assert witness != interpreter_line

    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", witness],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    assert "loopx-witness-ok" in completed.stdout


def test_installed_layout_prints_a_runnable_pip_fallback(tmp_path: Path) -> None:
    """Load the shipped module from an installed layout, not from a checkout."""

    layout = tmp_path / "site-packages"
    (layout / "loopx").mkdir(parents=True)
    source = Path(install_contract.__file__).resolve().parent
    for name in ("__init__.py", "install_contract.py"):
        shutil.copyfile(source / name, layout / "loopx" / name)
    probe = (
        "import loopx.install_contract as owner;"
        "print(owner.__file__);"
        "owner._is_windows_host = lambda: True;"
        "print(owner.archive_fallback_install_command())"
    )
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=layout,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert (
        Path(lines[0]).resolve() == (layout / "loopx" / "install_contract.py").resolve()
    )
    assert lines[1:] == [
        f"& {powershell_literal(sys.executable)} -m pip install --upgrade loopx",
        "loopx workflow-skills --install",
        "loopx doctor",
    ]


def test_bootstrap_payload_prints_commands_this_host_can_run(tmp_path: Path) -> None:
    repair, fallback = bootstrap_hint(tmp_path)
    assert "loopx doctor" in repair
    assert "loopx doctor" in fallback
    if os.name == "nt":
        for command in (repair, fallback):
            for token in POSIX_ONLY_TOKENS:
                assert token not in command, command
        assert Path(sys.executable).is_file()
        assert powershell_literal(sys.executable) in repair
        checkout_installer = (
            Path(install_contract.__file__).resolve().parents[1]
            / "scripts"
            / "install-windows.ps1"
        )
        assert checkout_installer.is_file()
        assert powershell_literal(str(checkout_installer)) in fallback
    else:
        assert repair == install_contract.DEFAULT_INSTALL_REPAIR_COMMAND
        assert fallback == install_contract.ARCHIVE_FALLBACK_INSTALL_COMMAND


def test_bootstrap_payload_without_the_checkout_installer_uses_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pretend_windows_host(monkeypatch, r"C:\Python311\python.exe")
    pretend_module_root(monkeypatch, tmp_path / "site-packages")

    _, fallback = bootstrap_hint(tmp_path)

    assert fallback.splitlines()[0] == (
        f"& {powershell_literal(PLAIN_WINDOWS_INTERPRETER)} "
        "-m pip install --upgrade loopx"
    )
    assert "install-windows.ps1" not in fallback
