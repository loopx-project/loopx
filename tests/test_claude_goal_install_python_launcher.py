r"""The Claude adapter installer must bake a launcher that actually runs Python.

`install.py --harden` writes a PreToolUse hook command and a statusline command
into `settings.json`, and `command_md()` writes the `/loopx` command with the
same interpreter. All of them bake one PATH lookup of `python3` - the first
interpreter name found, without ever running it.

On Windows, `shutil.which("python3")` can resolve to the 0-byte `WindowsApps`
App Execution Alias (`%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe`). That
file exists and passes `shutil.which`, but it never starts Python - on some
hosts executing it exits non-zero (9009), on others launching it raises OSError,
and it writes nothing either way. An installer that bakes that path writes hook /
statusline / `/loopx` commands that silently do nothing:
the `--harden` should_run gate fails OPEN (Claude Code treats the non-zero,
decision-less hook exit as a non-blocking error and runs the tool anyway), the
statusline renders nothing, and `/loopx` never runs.

The installer must only bake an interpreter it has confirmed can launch Python.
PR #6166 named this residual as its out-of-scope successor.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.claude_goal_mode.scripts import install as claude_install


def _dead_python3_alias(tmp_path: Path) -> Path:
    """A stand-in for the 0-byte `WindowsApps` App Execution Alias.

    Executing it raises OSError - WinError 193 ("not a valid Win32 application")
    on Windows, "Permission denied" on POSIX - so it has the same
    `shutil.which`-passes / cannot-run shape as the real alias, on any host.
    """
    alias = tmp_path / "WindowsApps" / "python3.EXE"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_bytes(b"")
    return alias


@pytest.fixture()
def alias_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PATH where `python3` is the dead alias and `python` is a real interpreter."""
    alias = _dead_python3_alias(tmp_path)

    def which(name: str) -> str | None:
        if name == "python3":
            return str(alias)
        if name == "python":
            return sys.executable
        return None

    monkeypatch.setattr(claude_install.shutil, "which", which)
    return alias


def test_python_cmd_skips_a_python3_alias_that_cannot_run(alias_host: Path) -> None:
    assert claude_install._python_cmd() == sys.executable


def test_python_cmd_falls_back_to_sys_executable_when_no_candidate_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = _dead_python3_alias(tmp_path)
    monkeypatch.setattr(claude_install.shutil, "which", lambda name: str(alias))
    assert claude_install._python_cmd() == sys.executable


def test_baked_commands_do_not_carry_the_dead_alias(alias_host: Path) -> None:
    alias = str(alias_host)
    hardening = claude_install.hardening_block()
    hook_cmd = hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    status_cmd = hardening["statusLine"]["command"]
    command = claude_install.command_md()

    assert sys.executable in hook_cmd and alias not in hook_cmd
    assert sys.executable in status_cmd and alias not in status_cmd
    assert f"allowed-tools: Bash({sys.executable}:*)" in command
    assert alias not in command


def test_installed_gate_command_actually_runs_python(alias_host: Path, tmp_path: Path) -> None:
    """Run exactly the hook command `--harden` installs, the way Claude Code does.

    Claude Code executes the command through a shell with the event JSON on
    stdin. With the dead alias baked in, the shell finds a file that exits 9009
    and prints nothing; the event never reaches the gate.
    """
    settings = tmp_path / "claude" / "settings.json"
    claude_install.add_hardening(dry=False, settings_path=settings)
    command = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    project = tmp_path / "project"
    project.mkdir()
    event = json.dumps({"cwd": str(project), "tool_name": "Read", "tool_input": {}})
    result = subprocess.run(command, shell=True, input=event.encode("utf-8"), capture_output=True)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert json.loads(result.stdout.decode("utf-8")) == {}


def test_python_cmd_keeps_the_python3_preference_when_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    tried: list[str] = []

    def launches(py: str) -> bool:
        tried.append(py)
        return True

    monkeypatch.setattr(claude_install.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(claude_install, "_launches_python", launches)
    assert claude_install._python_cmd() == "/fake/python3"
    assert tried == ["/fake/python3"]


def test_python_cmd_falls_through_python3_to_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_install.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(claude_install, "_launches_python", lambda py: py.endswith("python"))
    assert claude_install._python_cmd() == "/fake/python"


def test_launches_python_rejects_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 9009

    monkeypatch.setattr(claude_install.subprocess, "run", lambda *args, **kwargs: Result())
    assert claude_install._launches_python("python3") is False


def test_launches_python_accepts_a_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 0

    monkeypatch.setattr(claude_install.subprocess, "run", lambda *args, **kwargs: Result())
    assert claude_install._launches_python("python3") is True
