#!/usr/bin/env python3
"""Install the loopx Claude Code adapter — EXPLICIT, scoped, opt-in.

This is NOT run by the default loopx installer. It is invoked deliberately
(`LOOPX_INSTALL_CLAUDE=1 scripts/install-local.sh`, or directly). It installs the
minimum: the loopx **MCP server** (the control-plane tools) + the **`/loopx`**
slash command. The run loop is Claude Code's native `/loop`; loopx only provides
the `should_run` protocol, so NO global hooks are wired by default.

  python install.py --scope project [--project DIR]   # this project only (preferred)
  python install.py --scope user                      # all your Claude Code projects
  python install.py --scope ... --harden              # ALSO add the optional
                                                       # PreToolUse should_run gate
                                                       # + statusline at that scope

`--scope` is required (a deliberate choice). User scope is announced loudly. We
NEVER delete existing permission rules; legacy loopx entries get a printed
cleanup suggestion instead.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MCP_VENV = Path.home() / ".local" / "share" / "loopx" / "mcp-venv"

# This script is run by path (`python <release>/loopx/claude_goal_mode/scripts/
# install.py`), so the package root is not on `sys.path` by default.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.file_lock import write_json_atomic  # noqa: E402


def _p(*parts) -> str:
    return str(PLUGIN_ROOT.joinpath(*parts)).replace("\\", "/")


def _is_git_bash(path: str) -> bool:
    """True when `path` is the MSYS/Cygwin bash Claude Code dispatches to.

    The WSL `bash.exe` that sits on some PATHs is a Linux shell, not the Git
    Bash Claude Code spawns for a Windows command string, and its `uname` says
    `Linux`; the System32 alias is rejected by name and everything else by
    asking the candidate for its `uname`."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    resolved = os.path.normcase(os.path.realpath(path))
    system32_resolved = os.path.normcase(os.path.realpath(system32))
    if resolved == system32_resolved or resolved.startswith(system32_resolved + os.sep):
        return False
    try:
        probe = subprocess.run([path, "-c", "uname -s"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and probe.stdout.decode("utf-8", "replace").startswith(
        ("MINGW", "MSYS", "CYGWIN")
    )


# Claude Code honors `CLAUDE_CODE_GIT_BASH_PATH` only when the file name is one
# of these (env-vars reference, 2.1.219+); any other name is ignored and Git
# Bash detection continues as if the variable were unset - even when the file
# exists and runs.
_GIT_BASH_OVERRIDE_NAMES = frozenset({"bash.exe", "sh.exe", "bash", "sh"})


def _git_bash() -> str | None:
    """The Git Bash Claude Code runs a shell-form command string with, if any.

    Claude Code passes a hook or statusline string to Git Bash on Windows when
    Git Bash is installed, to PowerShell when it is not, and to `/bin/sh` on
    macOS/Linux (Claude Code hooks reference, "Exec form and shell form"). It
    finds Git Bash through its `CLAUDE_CODE_GIT_BASH_PATH` override, the Git
    installation or a `bash` on PATH, or a default Git for Windows location;
    this mirrors those sources so the encoding can follow the same choice. Like
    that consumer, an override whose file name is not `bash.exe`, `sh.exe`,
    `bash` or `sh` (compared case-insensitively) is ignored and detection
    continues as if it were unset."""
    if sys.platform != "win32":
        return None
    candidates: list[str] = []
    override = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if override and Path(override).name.lower() in _GIT_BASH_OVERRIDE_NAMES:
        candidates.append(override)
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent
        candidates += [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
    on_path = shutil.which("bash")
    if on_path:
        candidates.append(on_path)
    for base, sub in (
        (os.environ.get("ProgramFiles"), "Git"),
        (os.environ.get("ProgramFiles(x86)"), "Git"),
        (os.environ.get("LocalAppData"), "Programs/Git"),
    ):
        if base:
            candidates.append(str(Path(base) / sub / "bin" / "bash.exe"))
    for candidate in candidates:
        if os.path.isfile(candidate) and _is_git_bash(candidate):
            return candidate
    return None


def _uses_powershell() -> bool:
    """Whether Claude Code runs these command strings through PowerShell.

    A Windows host without Git Bash gets PowerShell for shell-form hooks and
    the statusline; every other host runs `/bin/sh` or Git Bash."""
    return sys.platform == "win32" and _git_bash() is None


def _shell_literal(value: str, *, powershell: bool) -> str:
    """Render `value` as one literal token the consumer shell reads back whole.

    Double quotes are not literal in either shell: `$NAME` expands inside them
    and an inner `"` can close the token. Single quotes are literal in both, so
    POSIX/Git Bash targets get `shlex.quote`'s form and PowerShell a `'...'`
    string with its `''` escape. A bare Windows path would lose its
    backslashes under Git Bash, so those are quoted too."""
    if powershell:
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def _shell_command(value: str, *, powershell: bool) -> str:
    """`_shell_literal` in command position: PowerShell needs its `&` operator."""
    token = _shell_literal(value, powershell=powershell)
    return f"& {token}" if powershell else token


def _launches_python(py: str) -> bool:
    """True when running `py` actually starts an interpreter.

    `shutil.which` only proves a file is executable, not that it runs Python. On
    Windows it can return the 0-byte `WindowsApps` App Execution Alias, which
    never starts Python: on some hosts it exits non-zero (9009), on others
    launching it raises OSError, and it writes nothing either way. Anything that
    bakes that path (the PreToolUse hook, the statusline, `/loopx`) then
    silently does nothing."""
    try:
        return subprocess.run([py, "-c", ""], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _python_cmd() -> str:
    """Interpreter to bake into hook / statusline / command strings — robust where
    only `python` (not `python3`) is registered, and where `python3` resolves to a
    launcher that cannot run Python (the Windows `WindowsApps` alias)."""
    for candidate in (shutil.which("python3"), shutil.which("python")):
        if candidate and _launches_python(candidate):
            return candidate
    return sys.executable


def deep_merge(base: dict, add: dict) -> dict:
    for k, v in add.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        elif isinstance(v, list) and isinstance(base.get(k), list):
            base[k] += [x for x in v if x not in base[k]]
        else:
            base[k] = v
    return base


# ---- optional hardening (only with --harden) --------------------------------

def hardening_block() -> dict:
    """The OPTIONAL per-tool gate + statusline. Not installed unless --harden."""
    powershell = _uses_powershell()
    py = _shell_command(_python_cmd(), powershell=powershell)
    gate = _shell_literal(_p("hooks", "goal_policy.py"), powershell=powershell)
    status = _shell_literal(_p("statusline", "goal_status.py"), powershell=powershell)
    return {
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": f"{py} {gate}", "timeout": 10}]}],
        },
        "statusLine": {"type": "command", "command": f"{py} {status}"},
    }


# ---- MCP dependency provisioning --------------------------------------------

def _venv_python(venv: Path) -> Path:
    sub = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    return venv / sub / exe


# The server imports `mcp.server.fastmcp`, which MCP SDK 2.x no longer ships.
# Checking a bare `import mcp` accepts an interpreter that cannot actually run
# the server, so registration would write a config that fails to start —
# `cursor-agent mcp list` reports "Connection failed" and Claude silently drops
# the tools. Probe the module the server really needs.
MCP_PROBE = "import mcp.server.fastmcp"
# Upper bound for the same reason; drop it once loopx_mcp.py speaks the 2.x API.
MCP_REQUIREMENT = "mcp<2"


def _has_mcp(py) -> bool:
    try:
        return subprocess.run([str(py), "-c", MCP_PROBE], capture_output=True).returncode == 0
    except (FileNotFoundError, OSError):
        return False


def provision_mcp_python(dry: bool, allow_system_pip: bool = False) -> str:
    """Return an interpreter that can `import mcp`, installing it into a dedicated
    venv if needed (works under PEP 668 externally-managed pythons).

    We install `mcp` ONLY into a dedicated venv we own. We never silently mutate
    the user's active/system interpreter (`pip install --break-system-packages`);
    that is gated behind an explicit ``allow_system_pip`` opt-in."""
    if _has_mcp(sys.executable):
        print(f"[deps] mcp already importable by {sys.executable}")
        return sys.executable
    vpy = _venv_python(MCP_VENV)
    if _has_mcp(vpy):
        print(f"[deps] reusing mcp venv {MCP_VENV}")
        return str(vpy)
    if dry:
        print(f"[deps] (dry-run) would create venv {MCP_VENV} and pip install mcp")
        return str(vpy)
    print(f"[deps] creating mcp venv {MCP_VENV} …")
    MCP_VENV.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, "-m", "venv", str(MCP_VENV)], capture_output=True, text=True, encoding="utf-8", errors="replace",)
    if r.returncode == 0:
        subprocess.run([str(vpy), "-m", "pip", "install", "-q", "--upgrade", "pip"], capture_output=True, text=True, encoding="utf-8", errors="replace",)
        pip = subprocess.run([str(vpy), "-m", "pip", "install", "-q", MCP_REQUIREMENT], capture_output=True, text=True, encoding="utf-8", errors="replace",)
        if pip.returncode == 0 and _has_mcp(vpy):
            print(f"[deps] mcp installed into {MCP_VENV}")
            return str(vpy)
        print("[deps] venv pip install failed:\n" + (pip.stdout + pip.stderr)[:300])
    else:
        print("[deps] venv creation failed:\n" + (r.stdout + r.stderr)[:300])
    # The dedicated venv is the only place we install `mcp`. Do NOT touch the
    # user's active/system interpreter unless they explicitly opted in.
    if allow_system_pip:
        print("[deps] --allow-system-pip: pip install --break-system-packages mcp")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", MCP_REQUIREMENT],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",)
        if _has_mcp(sys.executable):
            return sys.executable
    print("[deps] WARNING: could not provision `mcp` into a dedicated venv, and we will\n"
          "       NOT modify your system Python. The loopx MCP tools will not load until\n"
          "       you install `mcp` yourself, e.g.:\n"
          f"       {sys.executable} -m venv {MCP_VENV} && {vpy} -m pip install '{MCP_REQUIREMENT}'\n"
          "       (or re-run install.py with --allow-system-pip to use the active interpreter).")
    return _python_cmd()


def install_mcp(
    dry: bool,
    py: str,
    scope: str,
    migrate_goal_harness: bool = False,
    project_dir: Path | None = None,
) -> bool:
    """Register the loopx MCP server at the chosen scope via `claude mcp add`.

    We replace only OUR `loopx` entry. A legacy/user-owned `goal-harness` MCP is
    left untouched by default — we don't delete a server we don't own. If one
    exists we print a manual cleanup hint; `--migrate-goal-harness` removes it.

    Returns whether the `loopx` entry is registered afterwards: True only when
    `claude mcp add` reported success. A missing `claude` CLI and a failed add
    both leave the server unregistered and print the manual command instead, so
    a caller that narrates the install must read that receipt, not the attempt."""
    if scope == "project" and project_dir is None:
        raise ValueError("project_dir is required for project-scoped MCP registration")
    cwd_run = str(project_dir) if scope == "project" else None
    claude = shutil.which("claude")
    mcp_path = _p("mcp", "loopx_mcp.py")
    add = ["mcp", "add", "--scope", scope, "loopx", "--", py, mcp_path]
    if not claude:
        print("[mcp] claude CLI not on PATH — add the MCP server manually:\n   claude " + " ".join(add))
        return False
    print(f"[mcp] claude mcp add --scope {scope} loopx -- {py} <loopx_mcp.py>")
    if dry:
        return False
    subprocess.run(
        [claude, "mcp", "remove", "--scope", scope, "loopx"],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=cwd_run,
    )
    has_gh = subprocess.run(
        [claude, "mcp", "get", "goal-harness"],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=cwd_run,
    ).returncode == 0
    if has_gh:
        if migrate_goal_harness:
            print(f"[mcp] --migrate-goal-harness: removing legacy goal-harness MCP at {scope} scope")
            subprocess.run(
                [claude, "mcp", "remove", "--scope", scope, "goal-harness"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd_run,
            )
        else:
            print("[mcp] note: a legacy `goal-harness` MCP entry exists — left untouched. Remove it "
                  f"yourself with `claude mcp remove --scope {scope} goal-harness`, or re-run with "
                  "--migrate-goal-harness.")
    r = subprocess.run(
        [claude, *add],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=cwd_run,
    )
    if r.returncode != 0:
        print("  (mcp add failed; add manually:\n   claude " + " ".join(add) + "\n  " + (r.stdout + r.stderr)[:200] + ")")
        return False
    return True


# ---- /loopx command ----------------------------------------------------------

def command_md() -> str:
    # The `!` line runs through the Bash tool - `sh`/Git Bash on every host - so
    # it is always a POSIX literal, never the PowerShell form; a Windows host
    # without Git Bash cannot run `!` lines at all (README).
    entry = _shell_literal(_p("scripts", "goalmode_cmd.py"), powershell=False)
    py = _shell_literal(_python_cmd(), powershell=False)
    return (
        "---\n"
        "description: loopx goal-mode setup (NOT Claude Code's built-in /goal). "
        "`/loopx <task>` sets up a goal + writes .claude/loop.md; drive the loop with native /loop. "
        "bare /loopx = arm; off | status.\n"
        "argument-hint: <task to do>  |  (no args = arm)  |  off  |  status\n"
        f"allowed-tools: Bash({py}:*)\n"
        "---\n\n"
        "Run the loopx setup helper and read its output:\n\n"
        f"!`{py} {entry} $ARGUMENTS`\n\n"
        "The output is loopx control-plane SETUP / status info — it is the COMPLETE, user-facing "
        "result. Show it to the user VERBATIM and STOP. Do NOT do any work, claim todos, write "
        "files, or loop here: the WORK runs when the user types Claude Code's native `/loop` (which "
        "executes `.claude/loop.md`). Do not summarize a multi-line `status` detail block into one line.\n\n"
        "NOTE: this is `/loopx` (loopx control-plane SETUP), NOT a runtime and NOT Claude Code's "
        "built-in `/goal`. The run loop is native `/loop`; loopx provides the deterministic "
        "`should_run` protocol.\n"
    )


def install_command(dry: bool, cmd_dir: Path):
    path = cmd_dir / "loopx.md"
    print(f"[command] /loopx -> {path}")
    if dry:
        return
    cmd_dir.mkdir(parents=True, exist_ok=True)
    old = cmd_dir / "goalmode.md"  # migrate the legacy /goalmode entry name
    if old.exists():
        old.unlink()
    path.write_text(command_md(), encoding="utf-8")


class HardenSettingsUnreadable(RuntimeError):
    """`--harden` refuses to rewrite a settings file it cannot read as JSON."""


def add_hardening(dry: bool, settings_path: Path):
    """Deep-merge the optional hook + statusline into settings_path, preserving
    existing config. NEVER deletes permission rules.

    The merge rewrites the whole file, so a settings file that exists but does
    not parse is refused instead of replaced: substituting `{}` for it would
    drop every key the user owns - permission rules, `env`, `model`, other
    hooks, MCP approvals - leaving only the loopx block, at exit status 0. The
    file is user-owned (LoopX did not create it), which is why the sibling
    merge in `loopx/slash_command_install.py` fails closed on the same input.
    """
    cur = {}
    if settings_path.exists():
        try:
            cur = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HardenSettingsUnreadable(
                f"{settings_path} exists but is not readable as JSON ({exc}); refusing to "
                "rewrite it. Fix or move that file, then re-run with --harden."
            ) from exc
        if not isinstance(cur, dict):
            raise HardenSettingsUnreadable(
                f"{settings_path} exists but is not a JSON object; refusing to rewrite it. "
                "Fix or move that file, then re-run with --harden."
            )
    # prune prior loopx PreToolUse entries (avoid stacking on reinstall)
    for grp in (cur.get("hooks", {}) or {}).get("PreToolUse", []) or []:
        grp["hooks"] = [h for h in grp.get("hooks", []) if "goal_policy.py" not in str(h.get("command", ""))]
    if isinstance(cur.get("hooks", {}).get("PreToolUse"), list):
        cur["hooks"]["PreToolUse"] = [g for g in cur["hooks"]["PreToolUse"] if g.get("hooks")]
    merged = deep_merge(cur, hardening_block())
    print(f"[harden] PreToolUse should_run gate + statusline -> {settings_path}")
    if not dry:
        # One replace, not a truncate-in-place: an interrupted install must not
        # leave the user's settings.json as a partial document. The destination
        # is resolved first so a dotfiles-managed symlink keeps pointing at its
        # target instead of being replaced by the rename.
        write_json_atomic(settings_path.resolve(), merged, preserve_mode=True)


def warn_legacy_perms(settings_path: Path):
    """Detect a legacy loopx credential-deny and SUGGEST manual cleanup. Never mutate."""
    try:
        cur = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return
    deny = ((cur.get("permissions") or {}).get("deny")) or []
    legacy = [r for r in deny if r in ("Read(~/.ssh/**)", "Read(~/.aws/**)")]
    if legacy:
        print(f"[note] {settings_path} has loopx-era permission deny rules {legacy}. "
              "We no longer add these and will NOT remove them automatically. "
              "To drop them, edit that file's `permissions.deny` by hand.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["user", "project"], required=True,
                    help="user = ~/.claude (all projects); project = THIS project's .claude (preferred)")
    ap.add_argument("--project", default=None, help="project dir for --scope project (default: cwd)")
    ap.add_argument("--harden", action="store_true",
                    help="ALSO install the optional PreToolUse should_run gate + statusline")
    ap.add_argument("--skip-mcp", action="store_true",
                    help="skip MCP venv provisioning + `claude mcp add` (for tests/CI)")
    ap.add_argument("--allow-system-pip", action="store_true",
                    help="if the dedicated mcp venv can't be built, fall back to "
                         "`pip install --break-system-packages mcp` into the active interpreter "
                         "(off by default — we don't mutate your system Python)")
    ap.add_argument("--migrate-goal-harness", action="store_true",
                    help="also remove a legacy `goal-harness` MCP entry at this scope "
                         "(off by default — we don't delete MCP servers we don't own)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dry = a.dry_run

    project_dir = None
    if a.scope == "project":
        project_dir = Path(a.project).resolve() if a.project else Path.cwd()
        claude_dir = project_dir / ".claude"
        print(f"[scope] PROJECT — installs into {claude_dir} (this project only)")
    else:
        claude_dir = Path.home() / ".claude"
        print("[scope] USER — installs into ~/.claude. The /loopx command and MCP become")
        print("        available in ALL your Claude Code projects." +
              ("  --harden adds USER-LEVEL, matcher-wide hooks too." if a.harden else ""))

    mcp_registered = False
    if a.skip_mcp:
        print("[mcp] skipped (--skip-mcp)")
    else:
        mcp_python = provision_mcp_python(dry, a.allow_system_pip)
        mcp_registered = install_mcp(
            dry,
            mcp_python,
            a.scope,
            a.migrate_goal_harness,
            project_dir=project_dir,
        )
    install_command(dry, claude_dir / "commands")

    settings_path = claude_dir / "settings.json"
    if a.harden:
        try:
            add_hardening(dry, settings_path)
        except HardenSettingsUnreadable as exc:
            print(f"[harden] {exc}", file=sys.stderr)
            outcome = (
                "nothing was written (dry-run)"
                if dry
                else "the settings file is unchanged"
            )
            print(
                f"[harden] the optional should_run gate + statusline were skipped; {outcome}.",
                file=sys.stderr,
            )
            if not dry:
                # The rest of the install did run, so name only what happened:
                # the /loopx command was written just above, and the MCP state is
                # the registration receipt `install_mcp` returned - not the
                # attempt. A missing CLI or a failed `claude mcp add` leaves the
                # server unregistered, and reporting that attempt as an install
                # would let a user skip the manual command printed above.
                if a.skip_mcp:
                    mcp_state = "the MCP server was skipped (--skip-mcp)"
                elif mcp_registered:
                    mcp_state = "the MCP server was registered"
                else:
                    mcp_state = ("the MCP server was not registered — run the "
                                 "`claude mcp add` command printed above")
                print(
                    f"[harden] the /loopx command was installed; {mcp_state}.",
                    file=sys.stderr,
                )
            return 2
    else:
        print("[harden] skipped (default): no hooks/statusline installed. Add later with --harden.")
    warn_legacy_perms(settings_path)

    print("\nloopx Claude adapter installed.")
    print("Next: in a project with a goal, type `/loopx <task>`, then drive it with native `/loop`.")
    print("Restart any open Claude Code session so the command/MCP load.")
    if dry:
        print("(dry-run: nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
