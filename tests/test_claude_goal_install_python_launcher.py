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

The same baked strings interpolate that interpreter unquoted, so a confirmed
runnable interpreter under a path that contains a space - the python.org
all-users install (`C:\Program Files\Python312`), or any profile directory with
a space - produces a command the shell splits at the first space. PR #6229
named that residual and issue #6249 reported it; PR #6250 keeps every baked
token literal in the shell that actually runs it - `/bin/sh` on macOS/Linux,
Git Bash on Windows when it is installed, PowerShell on a Windows host without
Git Bash - and the `CLAUDE_CODE_GIT_BASH_PATH` override only counts there when
the file is named `bash.exe`, `sh.exe`, `bash` or `sh`, the same rule this
installer applies. cmd.exe was never that shell for current Claude Code, and it
read `$`, quotes and backslashes differently from the Git Bash that does run
these strings. These tests read the generated commands back out of the
installed files and execute them through the selected shell.
"""

from __future__ import annotations

import json
import shutil
import shlex
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
    assert f"allowed-tools: Bash({shlex.quote(sys.executable)}:*)" in command
    assert alias not in command


def test_installed_gate_command_actually_runs_python(alias_host: Path, tmp_path: Path) -> None:
    """Run exactly the hook command `--harden` installs, the way Claude Code does.

    Claude Code executes the command through a shell with the event JSON on
    stdin. With the dead alias baked in, the shell finds a file that exits 9009
    and prints nothing; the event never reaches the gate.
    """
    settings = tmp_path / "claude" / "settings.json"
    claude_install.add_hardening(dry=False, settings_path=settings)
    hook_cmd, _ = _installed_commands(settings)

    project = tmp_path / "project"
    project.mkdir()
    event = json.dumps({"cwd": str(project), "tool_name": "Read", "tool_input": {}})
    result = _host_shell_run(hook_cmd, event, cwd=project)

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


def _literal_interpreter(tmp_path: Path, dirname: str, request: pytest.FixtureRequest) -> Path:
    """A real launcher for `sys.executable` under `tmp_path / dirname`.

    On Windows the launcher is the running interpreter's base install directory
    behind a directory junction - the shape issue #6249's reproduction used. A
    copied `python.exe` cannot find its DLLs, a venv `python.exe` needs the
    `pyvenv.cfg` of the venv it was started from, and a `.bat` shim does not
    work: Git Bash hands it to cmd.exe, which splits a spaced shim path itself.
    The junction is dropped in teardown (`rmdir` unlinks it without touching the
    target); pytest's own `tmp_path` cleanup refuses to delete one.
    """
    home = tmp_path / dirname
    if sys.platform == "win32":
        probe = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(home), sys.base_prefix],
            capture_output=True,
        )
        if probe.returncode != 0:
            pytest.skip(f"cannot create a directory junction: {probe.stdout!r} {probe.stderr!r}")

        def _unlink() -> None:
            subprocess.run(["cmd.exe", "/d", "/c", "rmdir", str(home)], capture_output=True)

        request.addfinalizer(_unlink)
        return home / "python.exe"
    home.mkdir(parents=True, exist_ok=True)
    launcher = home / "python3"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


# Directory names the consumer shell must read back as one literal token, plus a
# plain one: a space used to split the token, `$` expanded, a backslash was
# eaten under Git Bash, and an inner `"` - which no Windows file name can hold -
# closed the token under `/bin/sh`.
_INTERPRETER_DIRNAMES = [
    "PlainPython311",
    "Program Files",
    "User $LABEL Home",
    pytest.param(
        'User "quoted" Home',
        marks=pytest.mark.skipif(
            sys.platform == "win32", reason='a Windows file name cannot contain "'
        ),
    ),
]


@pytest.fixture()
def literal_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Select a literal launcher as PATH's `python3` for one directory name."""
    real_which = shutil.which

    def make(dirname: str) -> Path:
        launcher = _literal_interpreter(tmp_path, dirname, request)
        monkeypatch.setattr(
            claude_install.shutil,
            "which",
            lambda name: str(launcher) if name == "python3" else real_which(name),
        )
        return launcher

    return make


def _host_shell_run(command: str, stdin: str, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run one baked command string through the shell Claude Code uses.

    A shell-form hook or statusline string goes to `/bin/sh` on macOS/Linux, to
    Git Bash on Windows when it is installed, and to PowerShell when it is not;
    `subprocess.run(..., shell=True)` would run cmd.exe on Windows, which is not
    that shell, so the selected one is spawned explicitly. The event or session
    JSON goes in on stdin.
    """
    data = stdin.encode("utf-8")
    if claude_install._uses_powershell():
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            input=data,
            capture_output=True,
            cwd=cwd,
        )
    if sys.platform == "win32":
        bash = claude_install._git_bash()
        assert bash, "a Windows rendering without Git Bash must be the PowerShell form"
        return subprocess.run([bash, "-c", command], input=data, capture_output=True, cwd=cwd)
    return subprocess.run(["/bin/sh", "-c", command], input=data, capture_output=True, cwd=cwd)


def _installed_commands(settings_path: Path) -> tuple[str, str]:
    """The hook and statusline command strings read back from settings.json."""
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    status = settings["statusLine"]["command"]
    return hook, status


def _expected_command_token(launcher: str) -> str:
    """The command token `hardening_block` must bake on this host for `launcher`."""
    if claude_install._uses_powershell():
        return f"& '{launcher}'"
    return shlex.quote(launcher)


def test_shell_literal_encodes_for_posix_sh() -> None:
    """Under `/bin/sh` and Git Bash the token must keep `$`, `"` and `\\` as data."""
    literal = claude_install._shell_literal
    assert literal("/usr/bin/python3", powershell=False) == "/usr/bin/python3"
    assert literal("/opt/my python/bin/python3", powershell=False) == "'/opt/my python/bin/python3'"
    assert (
        literal("/home/User $LABEL Home/python3", powershell=False)
        == "'/home/User $LABEL Home/python3'"
    )
    assert (
        literal('/home/User "quoted" Home/python3', powershell=False)
        == '\'/home/User "quoted" Home/python3\''
    )
    # Git Bash eats an unquoted backslash, so even a plain Windows path is quoted.
    assert literal(r"C:\Users\me\python.exe", powershell=False) == r"'C:\Users\me\python.exe'"


def test_shell_literal_and_command_encode_for_powershell() -> None:
    """PowerShell needs `'...'` with `''` escaping, and `&` in command position.

    Double quotes would still expand `$LABEL`, and a bare path is not a command
    at all outside command position.
    """
    literal = claude_install._shell_literal
    command = claude_install._shell_command
    assert literal(r"C:\Program Files (x86)\Python311\python.exe", powershell=True) == (
        r"'C:\Program Files (x86)\Python311\python.exe'"
    )
    assert command(r"C:\Users\User $LABEL Home\python.exe", powershell=True) == (
        r"& 'C:\Users\User $LABEL Home\python.exe'"
    )
    assert command("/usr/bin/python3", powershell=True) == "& '/usr/bin/python3'"
    assert literal("C:/it's/python.exe", powershell=True) == "'C:/it''s/python.exe'"


def test_shell_choice_follows_git_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    """PowerShell only on a Windows host that has no Git Bash for Claude Code.

    Claude Code passes a shell-form command string to Git Bash whenever Git Bash
    is installed on Windows, so the installer must not classify the host by
    `os.name` alone; everywhere else the shell is `/bin/sh`.
    """
    monkeypatch.setattr(claude_install, "_git_bash", lambda: None)
    assert claude_install._uses_powershell() is (sys.platform == "win32")
    monkeypatch.setattr(claude_install, "_git_bash", lambda: r"C:\Git\bin\bash.exe")
    assert claude_install._uses_powershell() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash discovery is Windows-only")
def test_git_bash_rejects_the_wsl_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""`C:\Windows\System32\bash.exe` is the WSL shell, not the Git Bash Claude spawns."""
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", r"C:\Windows\System32\bash.exe")
    monkeypatch.setattr(claude_install.shutil, "which", lambda name: None)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        monkeypatch.delenv(var, raising=False)
    assert claude_install._git_bash() is None


def _git_bash_host(
    monkeypatch: pytest.MonkeyPatch, which, override: str | None = None
) -> None:
    """Pin `_git_bash` to a Windows host whose only discovery sources are the
    `which` lookup and `override`.

    Pinning `sys.platform` covers the Windows-only rule on every host, and the
    Git-for-Windows default locations are cleared so a result can only come
    from the stub or the override.
    """
    monkeypatch.setattr(claude_install.sys, "platform", "win32")
    monkeypatch.setattr(claude_install.shutil, "which", which)
    if override is None:
        monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", override)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("name", ["custom-shell.exe", "git-bash.exe", "bash.cmd", "wsl-bash"])
def test_git_bash_ignores_override_names_claude_rejects(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `CLAUDE_CODE_GIT_BASH_PATH` name Claude Code does not accept is unset.

    The env-vars reference (2.1.219+) only honors `bash.exe`, `sh.exe`, `bash`
    and `sh`: any other name is ignored and detection continues, even though the
    file exists and runs. Accepting such an override here would render POSIX
    commands while the consumer dispatches the same string to PowerShell.
    """
    override = tmp_path / name
    override.write_bytes(b"")
    _git_bash_host(monkeypatch, lambda _name: None, override=str(override))
    # Only the consumer's name rule can reject the file: it exists and would
    # pass the MSYS probe.
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)
    assert claude_install._git_bash() is None


@pytest.mark.parametrize("name", ["bash.exe", "sh.exe", "bash", "sh", "BASH.EXE"])
def test_git_bash_honors_override_names_claude_accepts(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four accepted names win; the consumer lowercases, and so does this."""
    override = tmp_path / "Git" / "bin" / name
    override.parent.mkdir(parents=True)
    override.write_bytes(b"")
    _git_bash_host(monkeypatch, lambda _name: None, override=str(override))
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)
    assert claude_install._git_bash() == str(override)


def test_git_bash_ignores_a_rejected_name_but_keeps_detecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ignored override must not stop the auto-detection the consumer falls
    back to: a `git` on PATH still resolves its Git Bash."""
    override = tmp_path / "custom-shell.exe"
    override.write_bytes(b"")
    git = tmp_path / "Git" / "cmd" / "git.exe"
    git.parent.mkdir(parents=True)
    git.write_bytes(b"")
    fallback = tmp_path / "Git" / "bin" / "bash.exe"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"")
    _git_bash_host(
        monkeypatch, lambda name: str(git) if name == "git" else None, override=str(override)
    )
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)
    assert Path(claude_install._git_bash()).resolve() == fallback.resolve()


def test_rejected_override_renders_the_powershell_fallback(
    literal_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runnable `custom-shell.exe` must not flip the renderer to POSIX while
    the consumer ignores it and falls back.

    With no other Git Bash, Claude Code dispatches to PowerShell, so the
    installed hook and statusline must carry its `& '...'` command form.
    """
    launcher = str(literal_host("Program Files"))
    override = tmp_path / "custom-shell.exe"
    override.write_bytes(b"")
    _git_bash_host(
        monkeypatch, lambda name: launcher if name == "python3" else None, override=str(override)
    )
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)

    assert claude_install._uses_powershell() is True
    hardening = claude_install.hardening_block()
    hook = hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    status = hardening["statusLine"]["command"]
    assert hook.startswith(f"& '{launcher}' ")
    assert status.startswith(f"& '{launcher}' ")


def test_accepted_override_renders_the_literal_posix_form(
    literal_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override name the consumer honors keeps the literal POSIX rendering."""
    launcher = str(literal_host("Program Files"))
    override = tmp_path / "Git" / "bin" / "BASH.EXE"
    override.parent.mkdir(parents=True)
    override.write_bytes(b"")
    _git_bash_host(
        monkeypatch, lambda name: launcher if name == "python3" else None, override=str(override)
    )
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)

    assert claude_install._uses_powershell() is False
    token = shlex.quote(launcher)
    hardening = claude_install.hardening_block()
    hook = hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    status = hardening["statusLine"]["command"]
    assert hook.startswith(f"{token} ")
    assert status.startswith(f"{token} ")


def test_git_derived_fallback_renders_the_literal_posix_form(
    literal_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-detected Git Bash (no override) still renders the literal form."""
    launcher = str(literal_host("Program Files"))
    git = tmp_path / "Git" / "cmd" / "git.exe"
    git.parent.mkdir(parents=True)
    git.write_bytes(b"")
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"")
    _git_bash_host(
        monkeypatch,
        lambda name: launcher if name == "python3" else (str(git) if name == "git" else None),
    )
    monkeypatch.setattr(claude_install, "_is_git_bash", lambda _path: True)

    assert claude_install._uses_powershell() is False
    token = shlex.quote(launcher)
    hardening = claude_install.hardening_block()
    hook = hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    status = hardening["statusLine"]["command"]
    assert hook.startswith(f"{token} ")
    assert status.startswith(f"{token} ")


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash discovery is Windows-only")
def test_git_bash_is_a_runnable_msys_shell() -> None:
    """Whatever the discovery accepts must answer `uname -s` as an MSYS/Cygwin bash."""
    bash = claude_install._git_bash()
    if bash is None:
        pytest.skip("this host has no Git Bash")
    probe = subprocess.run([bash, "-c", "uname -s"], capture_output=True, timeout=10)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.decode("utf-8", "replace").startswith(("MINGW", "MSYS", "CYGWIN"))


@pytest.mark.skipif(sys.platform == "win32", reason="runs the rendering through /bin/sh")
@pytest.mark.parametrize(
    "path",
    [
        "/usr/bin/python3",
        "/opt/my python/bin/python3",
        "/home/User $LABEL Home/python3",
        '/home/User "quoted" Home/python3',
        "/home/it's/python3",
        "/home/back\\slash/python3",
    ],
)
def test_posix_rendering_round_trips_through_sh(path: str) -> None:
    """`/bin/sh` must expand the encoded token back to exactly the original path."""
    rendered = claude_install._shell_literal(path, powershell=False)
    result = subprocess.run(["/bin/sh", "-c", f"printf %s {rendered}"], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8") == path


@pytest.mark.parametrize("dirname", _INTERPRETER_DIRNAMES)
def test_baked_surfaces_keep_the_interpreter_token_literal(literal_host, dirname: str) -> None:
    launcher = str(literal_host(dirname))
    token = _expected_command_token(launcher)

    hardening = claude_install.hardening_block()
    assert hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith(f"{token} ")
    assert hardening["statusLine"]["command"].startswith(f"{token} ")

    # The `/loopx` `!` line runs through the Bash tool on every host, so that
    # surface is always POSIX-encoded even where the hook uses PowerShell.
    posix_token = shlex.quote(launcher)
    command = claude_install.command_md()
    # The /loopx permission rule must carry the same token the command runs.
    assert f"allowed-tools: Bash({posix_token}:*)" in command
    body = next(line for line in command.splitlines() if line.startswith("!`"))
    assert body.startswith(f"!`{posix_token} ")


@pytest.mark.skipif(
    sys.platform == "win32", reason="a Windows path is always quoted for Git Bash"
)
def test_a_plain_posix_path_keeps_its_previous_rendering(literal_host) -> None:
    launcher = str(literal_host("PlainPython311"))
    assert claude_install._shell_literal(launcher, powershell=False) == launcher

    hardening = claude_install.hardening_block()
    assert hardening["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith(f"{launcher} ")
    assert hardening["statusLine"]["command"].startswith(f"{launcher} ")
    assert f"allowed-tools: Bash({launcher}:*)" in claude_install.command_md()


@pytest.mark.parametrize("dirname", _INTERPRETER_DIRNAMES)
def test_installed_gate_command_runs_through_a_literal_interpreter(
    literal_host, dirname: str, tmp_path: Path
) -> None:
    """Execute the hook command exactly as `--harden` installed it.

    A token the shell splits, expands or dequotes never reaches Python: the
    gate exits non-zero with empty stdout (rc 127 under `/bin/sh`), which
    Claude Code treats as a non-blocking hook error - the tool call proceeds
    ungated.
    """
    literal_host(dirname)
    settings = tmp_path / "claude" / "settings.json"
    claude_install.add_hardening(dry=False, settings_path=settings)
    hook_cmd, _ = _installed_commands(settings)

    project = tmp_path / "project"
    project.mkdir()
    event = json.dumps({"cwd": str(project), "tool_name": "Read", "tool_input": {}})
    result = _host_shell_run(hook_cmd, event, cwd=project)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert json.loads(result.stdout.decode("utf-8")) == {}


@pytest.mark.parametrize("dirname", _INTERPRETER_DIRNAMES)
def test_installed_statusline_command_renders_through_a_literal_interpreter(
    literal_host, dirname: str, tmp_path: Path
) -> None:
    """Execute the statusline command read back from settings.json.

    Claude Code feeds the session JSON on stdin; with no goal here the
    statusline must render nothing - but only after the interpreter starts.
    """
    literal_host(dirname)
    settings = tmp_path / "claude" / "settings.json"
    claude_install.add_hardening(dry=False, settings_path=settings)
    _, status_cmd = _installed_commands(settings)

    project = tmp_path / "project"
    project.mkdir()
    result = _host_shell_run(status_cmd, json.dumps({"cwd": str(project)}), cwd=project)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout.decode("utf-8").strip() == ""


@pytest.mark.parametrize("dirname", _INTERPRETER_DIRNAMES)
def test_installed_slash_command_runs_through_a_literal_interpreter(
    literal_host, dirname: str, tmp_path: Path
) -> None:
    """Execute the `/loopx` body line from the installed command file.

    Claude Code substitutes `$ARGUMENTS` textually and runs the line through
    the Bash tool (`sh`/Git Bash); `status` is the cheapest real argument, and
    with no goal here the helper says so and exits 0.
    """
    literal_host(dirname)
    cmd_dir = tmp_path / "claude" / "commands"
    claude_install.install_command(dry=False, cmd_dir=cmd_dir)
    text = (cmd_dir / "loopx.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if line.startswith("!`") and line.endswith("`"))
    command = line[2:-1].replace("$ARGUMENTS", "status")

    project = tmp_path / "project"
    project.mkdir()
    result = _host_shell_run(command, "", cwd=project)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "no goal in this project yet" in result.stdout.decode("utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="powershell.exe is the Windows shell")
def test_installed_commands_run_under_powershell_without_git_bash(
    literal_host, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rejected-override fallback must start the interpreter under the real
    PowerShell.

    This host may have Git Bash (CI Windows does), so every discovery source is
    pinned empty and the `CLAUDE_CODE_GIT_BASH_PATH` override carries a name
    Claude Code ignores, leaving the real selector on the PowerShell fallback.
    The installed strings are executed through a real `powershell.exe` - the
    shell Claude Code uses when Git Bash is absent - with the event and session
    JSON on stdin.
    """
    launcher = str(literal_host("Program Files"))
    override = tmp_path / "custom-shell.exe"
    override.write_bytes(b"")
    _git_bash_host(
        monkeypatch, lambda name: launcher if name == "python3" else None, override=str(override)
    )

    assert claude_install._uses_powershell() is True
    settings = tmp_path / "claude" / "settings.json"
    claude_install.add_hardening(dry=False, settings_path=settings)
    hook_cmd, status_cmd = _installed_commands(settings)
    assert hook_cmd.startswith(f"& '{launcher}'")
    assert status_cmd.startswith(f"& '{launcher}'")

    project = tmp_path / "project"
    project.mkdir()

    def run(command: str, stdin: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            input=stdin.encode("utf-8"),
            capture_output=True,
            cwd=project,
        )

    event = json.dumps({"cwd": str(project), "tool_name": "Read", "tool_input": {}})
    hook = run(hook_cmd, event)
    assert hook.returncode == 0, (hook.returncode, hook.stdout, hook.stderr)
    assert json.loads(hook.stdout.decode("utf-8-sig")) == {}

    status = run(status_cmd, json.dumps({"cwd": str(project)}))
    assert status.returncode == 0, (status.returncode, status.stdout, status.stderr)
    assert status.stdout.decode("utf-8-sig").strip() == ""
