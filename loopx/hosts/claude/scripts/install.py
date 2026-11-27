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

from typing import Any, NamedTuple

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MCP_VENV = Path.home() / ".local" / "share" / "loopx" / "mcp-venv"

# This script is run by path (`python <release>/loopx/hosts/claude/scripts/
# install.py`), so the package root is not on `sys.path` by default.
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.file_lock import write_bytes_atomic, write_json_atomic  # noqa: E402


def _p(*parts: str) -> str:
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
    """True when running `py` starts a supported Python 3.11+ interpreter.

    `shutil.which` only proves a file is executable, not that it runs Python. On
    Windows it can return the 0-byte `WindowsApps` App Execution Alias, which
    never starts Python: on some hosts it exits non-zero (9009), on others
    launching it raises OSError, and it writes nothing either way. Anything that
    bakes that path (the PreToolUse hook, the statusline, `/loopx`) then
    silently does nothing."""
    try:
        return subprocess.run(
            [py, "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"],
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _python_cmd() -> str:
    """Interpreter to bake into hook / statusline / command strings — robust where
    only `python` (not `python3`) is registered, and where `python3` resolves to a
    launcher that cannot run supported Python (including the Windows
    `WindowsApps` alias and an older PATH interpreter)."""
    for candidate in (shutil.which("python3"), shutil.which("python")):
        if candidate and _launches_python(candidate):
            return candidate
    return sys.executable


def deep_merge(base: dict[str, Any], add: dict[str, Any]) -> dict[str, Any]:
    for k, v in add.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        elif isinstance(v, list) and isinstance(base.get(k), list):
            base[k] += [x for x in v if x not in base[k]]
        else:
            base[k] = v
    return base


# ---- optional hardening (only with --harden) --------------------------------

def hardening_block() -> dict[str, Any]:
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


def _has_mcp(py: str | Path) -> bool:
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
        "description: loopx goal-mode setup. "
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
        "The run loop is native `/loop`; loopx provides the deterministic "
        "`should_run` protocol.\n"
    )


def install_command(dry: bool, cmd_dir: Path) -> None:
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


def add_hardening(dry: bool, settings_path: Path) -> None:
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


def warn_legacy_perms(settings_path: Path) -> None:
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


def main() -> int:
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


_LEGACY_COMMAND_SCRIPT_SUFFIX = "/loopx/claude_goal_mode/scripts/goalmode_cmd.py"
_LEGACY_POLICY_HOOK_SUFFIX = "/loopx/claude_goal_mode/hooks/goal_policy.py"
_LEGACY_STATUSLINE_SCRIPT_SUFFIX = "/loopx/claude_goal_mode/statusline/goal_status.py"
_LEGACY_MCP_SCRIPT_SUFFIX = "/loopx/claude_goal_mode/mcp/loopx_mcp.py"
_LEGACY_COMMAND_LINE_RE = re.compile(
    r"^!`[^`\r\n]*/loopx/claude_goal_mode/scripts/goalmode_cmd\.py[^`\r\n]*`\s*$"
)
_ALLOWED_TOOLS_BASH_RE = re.compile(r"^allowed-tools:\s*Bash\([^\r\n]*\)\s*$")


def _migration_python_cmd() -> str:
    """Prefer the running LoopX Python interpreter so upgraded venv commands resolve `loopx`."""
    if sys.executable and _launches_python(sys.executable):
        return sys.executable
    return _python_cmd()


def _resolve_live_python_command(
    raw_cmd: str,
    *,
    live_py_cache: dict[str, bool] | None = None,
) -> bool:
    """True when ``raw_cmd`` resolves to an executable Python 3.11+ interpreter."""
    if not raw_cmd:
        return False
    if live_py_cache is not None and raw_cmd in live_py_cache:
        return live_py_cache[raw_cmd]
    candidate_path = Path(raw_cmd)
    if "/" in raw_cmd or "\\" in raw_cmd:
        resolved = (
            str(candidate_path)
            if candidate_path.is_absolute() and candidate_path.is_file()
            else None
        )
    else:
        resolved = shutil.which(raw_cmd)
    ok = bool(resolved) and _launches_python(resolved)  # type: ignore[arg-type]
    if live_py_cache is not None:
        live_py_cache[raw_cmd] = ok
    return ok


def _is_live_legacy_shim_path(raw_path: str, *, legacy_suffix: str) -> bool:
    """True when ``raw_path`` resolves to this install's active ``claude_goal_mode`` shim."""
    normalized = raw_path.replace("\\", "/")
    if not normalized.endswith(legacy_suffix):
        return False
    expected_shim = REPO_ROOT / legacy_suffix.lstrip("/")
    if not expected_shim.is_file():
        return False
    candidate = Path(raw_path)
    if not candidate.is_file():
        return False
    try:
        return os.path.normcase(str(candidate.resolve())) == os.path.normcase(
            str(expected_shim.resolve())
        )
    except OSError:
        return False


def _migrated_command_md_text(
    raw: str,
    *,
    py_exe: str,
    live_py_cache: dict[str, bool] | None = None,
) -> str | None:
    if _LEGACY_COMMAND_SCRIPT_SUFFIX not in raw.replace("\\", "/"):
        return None
    if "description: loopx goal-mode setup" not in raw:
        return None
    lines = raw.splitlines(keepends=True)
    command_index: int | None = None
    allowed_index: int | None = None
    for index, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        if _LEGACY_COMMAND_LINE_RE.match(bare.replace("\\", "/")):
            command_index = index
        elif allowed_index is None and _ALLOWED_TOOLS_BASH_RE.match(bare):
            allowed_index = index
    if command_index is None:
        return None
    bare_cmd_line = lines[command_index].rstrip("\r\n")
    leading_ws_len = len(bare_cmd_line) - len(bare_cmd_line.lstrip())
    inner_cmd = bare_cmd_line.strip()[2:-1]
    migrated_inner, _ = _inspect_legacy_claude_command(
        inner_cmd,
        legacy_suffix=_LEGACY_COMMAND_SCRIPT_SUFFIX,
        new_script=_p("scripts", "goalmode_cmd.py"),
        py_exe=py_exe,
        powershell=False,
        live_py_cache=live_py_cache,
    )
    if migrated_inner is None:
        return None
    py = _shell_literal(py_exe, powershell=False)
    cmd_nl = "\r\n" if lines[command_index].endswith("\r\n") else "\n"
    lines[command_index] = f"{bare_cmd_line[:leading_ws_len]}!`{migrated_inner}`{cmd_nl}"
    if allowed_index is not None:
        allowed_nl = "\r\n" if lines[allowed_index].endswith("\r\n") else "\n"
        lines[allowed_index] = f"allowed-tools: Bash({py}:*){allowed_nl}"
    updated = "".join(lines)
    return updated if updated != raw else None


_PYTHON_EXE_NAME_RE = re.compile(
    r"^(?:pypy3?|python(?:w|3(?:\.\d+)?)?)(?:\.exe)?$"
)
_PYTHON_PASSTHROUGH_FLAGS = frozenset(
    {"-B", "-E", "-I", "-O", "-OO", "-P", "-S", "-s", "-u", "-v", "-q"}
)
_PYTHON_SHORT_FLAG_CHARS = frozenset("BbEdiIOPqRsSuvx")
_PYTHON_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
_PYTHON_TERMINAL_FLAGS = frozenset({"-c", "-m", "-"})
_SHELL_OPERATORS_ORDERED = (
    "<<-",
    "<<<",
    ";;&",
    "&&",
    "||",
    "|&",
    ";;",
    ";&",
    ">>",
    ">&",
    "<&",
    "<<",
    "<>",
    ";",
    "|",
    "&",
    "(",
    ")",
    "<",
    ">",
    "\n",
)
_SHELL_OPERATOR_CHARS = frozenset(";|&()<>\n")
_REDIRECTION_OPS = frozenset({"<<-", "<<<", ">>", ">&", "<&", "<<", "<>", "<", ">"})
_ENV_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-P", "-a", "--argv0"})
_TIMEOUT_VALUE_FLAGS = frozenset({"-k", "--kill-after", "-s", "--signal"})
_SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ash", "ksh"})
_CLAUSE_STARTERS = frozenset({"if", "elif", "then", "else", "while", "until", "do", "{"})
_CLAUSE_HEADERS_NON_COMMAND = frozenset({"for", "select", "case"})


class _ShellToken(NamedTuple):
    start: int
    end: int
    unquoted: str
    is_operator: bool = False


def _consume_heredoc_bodies(
    raw_command: str, i: int, pending_heredocs: list[tuple[str, bool]]
) -> int | None:
    """Skip pending POSIX here-document bodies and delimiters starting after `\\n`."""
    n = len(raw_command)
    for delim, strip_tabs in pending_heredocs:
        closed = False
        while i < n:
            line_end = raw_command.find("\n", i)
            if line_end == -1:
                line_end = n
            line_text = raw_command[i:line_end].rstrip("\r")
            candidate = line_text.lstrip("\t") if strip_tabs else line_text
            i = line_end + 1 if line_end < n else n
            if candidate == delim:
                closed = True
                break
        if not closed:
            return None
    return i


def _tokenize_shell_spans(
    raw_command: str, *, powershell: bool
) -> list[_ShellToken] | None:
    """Return word/operator tokens with original offsets, or ``None`` on shell syntax failure."""
    n = len(raw_command)
    i = 0
    tokens: list[_ShellToken] = []
    awaiting_heredoc: bool | None = None
    pending_heredocs: list[tuple[str, bool]] = []
    while i < n:
        while i < n and (
            raw_command[i] in " \t\r\v\f"
            or (not powershell and raw_command.startswith("\\\n", i))
        ):
            i += 2 if (not powershell and raw_command.startswith("\\\n", i)) else 1
        if i >= n:
            break
        if not powershell and awaiting_heredoc is None and raw_command[i] == "#":
            while i < n and raw_command[i] != "\n":
                i += 1
            continue
        matched_op: str | None = None
        if raw_command[i] in _SHELL_OPERATOR_CHARS:
            for candidate in _SHELL_OPERATORS_ORDERED:
                if raw_command.startswith(candidate, i):
                    matched_op = candidate
                    break
        if matched_op is not None:
            if awaiting_heredoc is not None:
                return None
            tokens.append(_ShellToken(i, i + len(matched_op), matched_op, is_operator=True))
            i += len(matched_op)
            if not powershell and matched_op in {"<<", "<<-"}:
                awaiting_heredoc = matched_op == "<<-"
            elif not powershell and matched_op == "\n" and pending_heredocs:
                next_i = _consume_heredoc_bodies(raw_command, i, pending_heredocs)
                if next_i is None:
                    return None
                pending_heredocs.clear()
                i = next_i
            continue

        start = i
        word_end = i
        buf: list[str] = []
        while i < n and raw_command[i] not in " \t\r\v\f" and raw_command[i] not in _SHELL_OPERATOR_CHARS:
            ch = raw_command[i]
            if ch == "'":
                i += 1
                closed = False
                while i < n:
                    if raw_command[i] == "'":
                        if powershell and i + 1 < n and raw_command[i + 1] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        closed = True
                        break
                    buf.append(raw_command[i])
                    i += 1
                if not closed:
                    return None
                word_end = i
            elif ch == '"':
                i += 1
                while i < n and raw_command[i] != '"':
                    if (
                        not powershell
                        and raw_command[i] == "\\"
                        and i + 1 < n
                        and raw_command[i + 1] == "\n"
                    ):
                        i += 2
                        continue
                    if (
                        raw_command[i] == "\\"
                        and i + 1 < n
                        and raw_command[i + 1] == '"'
                    ):
                        buf.append('"')
                        i += 2
                        continue
                    buf.append(raw_command[i])
                    i += 1
                if i >= n:
                    return None
                i += 1
                word_end = i
            elif (
                ch == "\\"
                and not powershell
                and i + 1 < n
                and (
                    awaiting_heredoc is not None
                    or raw_command[i + 1].isspace()
                    or raw_command[i + 1] in {"'", '"', "\\", "|", "&", ";", "(", ")", "<", ">"}
                )
            ):
                if raw_command[i + 1] == "\n":
                    if awaiting_heredoc is not None:
                        return None
                    i += 2
                    continue
                i += 1
                buf.append(raw_command[i])
                i += 1
                word_end = i
            else:
                buf.append(ch)
                i += 1
                word_end = i
        unquoted_word = "".join(buf)
        tokens.append(_ShellToken(start, word_end, unquoted_word))
        if awaiting_heredoc is not None:
            pending_heredocs.append((unquoted_word, awaiting_heredoc))
            awaiting_heredoc = None
    if awaiting_heredoc is not None or pending_heredocs:
        return None
    return tokens


def _looks_like_python_token(unquoted: str) -> bool:
    if "=" in unquoted:
        return False
    name = Path(unquoted.replace("\\", "/")).name.lower()
    return bool(_PYTHON_EXE_NAME_RE.fullmatch(name))


def _is_unquoted_env_assignment(token: _ShellToken, *, raw_command: str) -> bool:
    if not _ENV_ASSIGN_RE.fullmatch(token.unquoted):
        return False
    eq_pos = raw_command.find("=", token.start, token.end)
    if eq_pos <= token.start:
        return False
    prefix = raw_command[token.start : eq_pos]
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix))


def _split_command_segments(
    tokens: list[_ShellToken], *, raw_command: str, powershell: bool
) -> list[tuple[list[_ShellToken], bool]]:
    segments: list[tuple[list[_ShellToken], bool]] = []
    words: list[_ShellToken] = []
    has_ps_call = False
    in_case_header = False
    case_depth = 0
    expect_case_pattern = False
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.is_operator:
            if tok.unquoted in _REDIRECTION_OPS:
                if words and words[-1].end == tok.start and words[-1].unquoted.isdigit():
                    words.pop()
                if idx + 1 < len(tokens) and not tokens[idx + 1].is_operator:
                    idx += 2
                else:
                    idx += 1
                continue
            if tok.unquoted == "&" and powershell and not words and not has_ps_call:
                has_ps_call = True
                idx += 1
                continue
            if case_depth > 0 and expect_case_pattern:
                if tok.unquoted == ")":
                    expect_case_pattern = False
                    words = []
                    has_ps_call = False
                idx += 1
                continue
            if words:
                segments.append((words, has_ps_call))
            words = []
            has_ps_call = False
            in_case_header = False
            if case_depth > 0 and tok.unquoted in {";;", ";&", ";;&"}:
                expect_case_pattern = True
            idx += 1
            continue

        raw_slice = raw_command[tok.start : tok.end]
        if not powershell:
            if case_depth > 0 and expect_case_pattern:
                if not words and raw_slice == "esac":
                    case_depth -= 1
                    expect_case_pattern = False
                    idx += 1
                    continue
                idx += 1
                continue
            if case_depth > 0 and not words and raw_slice == "esac":
                case_depth -= 1
                idx += 1
                continue
            if in_case_header and raw_slice == "in":
                in_case_header = False
                case_depth += 1
                expect_case_pattern = True
                words = []
                idx += 1
                continue
            if not words and raw_slice == "case":
                in_case_header = True

        words.append(tok)
        idx += 1
    if words and not (case_depth > 0 and expect_case_pattern):
        segments.append((words, has_ps_call))
    return segments


def _advance_command_wrappers(
    words: list[_ShellToken],
    *,
    raw_command: str,
    legacy_suffix: str,
    powershell: bool,
) -> tuple[int | None, str | None]:
    idx = 0
    while idx < len(words):
        if not powershell:
            tok = words[idx]
            raw_slice = raw_command[tok.start : tok.end]
            if raw_slice == tok.unquoted and tok.unquoted in _CLAUSE_STARTERS:
                idx += 1
                continue
            if raw_slice == tok.unquoted and tok.unquoted in _CLAUSE_HEADERS_NON_COMMAND:
                return (None, None)
            if _is_unquoted_env_assignment(tok, raw_command=raw_command):
                idx += 1
                continue
            if tok.unquoted == "!" and raw_slice == "!":
                idx += 1
                continue
        if powershell:
            break

        cmd_word = words[idx].unquoted
        cmd_name = Path(cmd_word.replace("\\", "/")).name.lower()
        has_legacy_tail = any(
            legacy_suffix in w.unquoted.replace("\\", "/") for w in words[idx:]
        )
        probe = idx + 1
        if cmd_name == "env":
            while probe < len(words):
                arg = words[probe].unquoted
                if arg == "--":
                    probe += 1
                    break
                if arg in {"-S", "--split-string"} or arg.startswith(("-S", "--split-string=")):
                    return (
                        None,
                        "unsupported `env -S` wrapper around legacy Claude command"
                        if has_legacy_tail
                        else None,
                    )
                if arg in _ENV_VALUE_FLAGS:
                    probe += 2
                    continue
                if arg.startswith("-") or _ENV_ASSIGN_RE.fullmatch(arg):
                    probe += 1
                    continue
                break
            if probe > len(words):
                return (None, None)
            idx = probe
            continue
        if cmd_word == "command":
            while probe < len(words) and words[probe].unquoted.startswith("-") and words[probe].unquoted != "--":
                flag = words[probe].unquoted
                if "v" in flag or "V" in flag:
                    return (None, None)
                if flag != "-p":
                    return (
                        None,
                        f"unsupported `command {flag}` option around legacy Claude command"
                        if has_legacy_tail
                        else None,
                    )
                probe += 1
            if probe < len(words) and words[probe].unquoted == "--":
                probe += 1
            idx = probe
            continue
        if cmd_word == "exec":
            while probe < len(words) and words[probe].unquoted.startswith("-"):
                flag = words[probe].unquoted
                if flag == "--":
                    probe += 1
                    break
                if flag == "-a":
                    probe += 2
                elif len(flag) > 1 and set(flag[1:]) <= {"c", "l"}:
                    probe += 1
                else:
                    return (
                        None,
                        f"unsupported `exec {flag}` option around legacy Claude command"
                        if has_legacy_tail
                        else None,
                    )
            if probe > len(words):
                return (None, None)
            idx = probe
            continue
        if cmd_word in {"nohup", "time"} or cmd_name == "nice":
            if cmd_word == "time" and probe < len(words) and words[probe].unquoted == "-p":
                probe += 1
            elif cmd_name == "nice" and probe < len(words):
                arg = words[probe].unquoted
                if arg in {"-n", "--adjustment"}:
                    probe += 2
                elif arg.startswith("--adjustment=") or (
                    arg.startswith("-") and arg[1:].lstrip("+-").isdigit()
                ):
                    probe += 1
            if probe > len(words):
                return (None, None)
            idx = probe
            continue
        if cmd_name == "timeout":
            while probe < len(words) and words[probe].unquoted.startswith("-"):
                arg = words[probe].unquoted
                if arg == "--":
                    probe += 1
                    break
                if arg in _TIMEOUT_VALUE_FLAGS:
                    probe += 2
                elif arg.startswith(("--kill-after=", "--signal=")) or arg in {
                    "-v",
                    "--verbose",
                    "--foreground",
                    "--preserve-status",
                }:
                    probe += 1
                else:
                    return (
                        None,
                        f"unsupported `timeout {arg}` option around legacy Claude command"
                        if has_legacy_tail
                        else None,
                    )
            if probe >= len(words):
                return (None, None)
            dur = words[probe].unquoted
            if dur.startswith("-") or dur.replace("\\", "/").endswith(legacy_suffix):
                return (None, None)
            idx = probe + 1
            continue
        break
    return (idx, None) if idx < len(words) else (None, None)


def _segment_edits(
    words: list[_ShellToken],
    *,
    has_ps_call: bool,
    raw_command: str,
    legacy_suffix: str,
    new_script: str,
    py_exe: str,
    powershell: bool,
    live_py_cache: dict[str, bool] | None = None,
) -> tuple[list[tuple[int, int, str]], str | None]:
    cmd_idx, wrapper_reason = _advance_command_wrappers(
        words,
        raw_command=raw_command,
        legacy_suffix=legacy_suffix,
        powershell=powershell,
    )
    if cmd_idx is None:
        return ([], wrapper_reason)

    cmd_tok = words[cmd_idx]
    cmd_name = Path(cmd_tok.unquoted.replace("\\", "/")).name.lower()
    if not powershell and cmd_tok.unquoted == "eval":
        if any(legacy_suffix in w.unquoted.replace("\\", "/") for w in words[cmd_idx + 1 :]):
            return ([], "dynamic `eval` command references legacy Claude launcher")
        return ([], None)

    if cmd_name.removesuffix(".exe") in _SHELL_NAMES and not cmd_tok.unquoted.startswith("-"):
        probe = cmd_idx + 1
        saw_dash_c = False
        while probe < len(words):
            arg = words[probe].unquoted
            if arg == "--":
                probe += 1
                break
            if not arg.startswith("-") and not (arg.startswith("+") and len(arg) > 1):
                break
            if arg in {"-o", "+o"}:
                probe += 2
                continue
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
                saw_dash_c = True
                probe += 1
                break
            probe += 1
        if not saw_dash_c or probe >= len(words):
            return ([], None)
        inner_tok = words[probe]
        if legacy_suffix not in inner_tok.unquoted.replace("\\", "/"):
            return ([], None)
        inner, inner_reason = _inspect_legacy_claude_command(
            inner_tok.unquoted,
            legacy_suffix=legacy_suffix,
            new_script=new_script,
            py_exe=py_exe,
            powershell=False,
            live_py_cache=live_py_cache,
        )
        if inner is None:
            return ([], inner_reason)
        return ([(inner_tok.start, inner_tok.end, _shell_literal(inner, powershell=powershell))], inner_reason)

    needs_ps_call = powershell and not (has_ps_call and cmd_idx == 0)
    py_replacement = (
        _shell_command(py_exe, powershell=True)
        if needs_ps_call
        else _shell_literal(py_exe, powershell=powershell)
    )
    script_literal = _shell_literal(new_script, powershell=powershell)

    if _looks_like_python_token(cmd_tok.unquoted):
        probe = cmd_idx + 1
        while probe < len(words):
            arg = words[probe].unquoted
            if arg in _PYTHON_TERMINAL_FLAGS or arg.startswith(("-c", "-m")):
                return ([], None)
            if arg in _PYTHON_VALUE_FLAGS:
                probe += 2
                if probe > len(words):
                    return ([], None)
                continue
            if arg.startswith(("-W", "-X", "--check-hash-based-pycs=")):
                probe += 1
                continue
            if arg in _PYTHON_PASSTHROUGH_FLAGS or (
                len(arg) > 1
                and arg[0] == "-"
                and not arg.startswith("--")
                and set(arg[1:]) <= _PYTHON_SHORT_FLAG_CHARS
            ):
                probe += 1
                continue
            if arg.startswith("-"):
                has_legacy_tail = any(
                    legacy_suffix in w.unquoted.replace("\\", "/") for w in words[probe + 1 :]
                )
                return (
                    [],
                    f"unsupported Python option `{arg}` around legacy Claude launcher"
                    if has_legacy_tail
                    else None,
                )
            break
        if probe >= len(words):
            return ([], None)
        script_tok = words[probe]
        normalized = script_tok.unquoted.replace("\\", "/")
        if not normalized.startswith("-") and normalized.endswith(legacy_suffix):
            if _is_live_legacy_shim_path(
                script_tok.unquoted, legacy_suffix=legacy_suffix
            ) and _resolve_live_python_command(
                cmd_tok.unquoted, live_py_cache=live_py_cache
            ):
                return ([], None)
            return (
                [
                    (script_tok.start, script_tok.end, script_literal),
                    (cmd_tok.start, cmd_tok.end, py_replacement),
                ],
                None,
            )
        return ([], None)

    normalized_cmd = cmd_tok.unquoted.replace("\\", "/")
    if (
        not normalized_cmd.startswith("-")
        and not _ENV_ASSIGN_RE.fullmatch(normalized_cmd)
        and normalized_cmd.endswith(legacy_suffix)
    ):
        if (
            os.name != "nt"
            and _is_live_legacy_shim_path(
                cmd_tok.unquoted, legacy_suffix=legacy_suffix
            )
            and os.access(Path(cmd_tok.unquoted), os.X_OK)
            and _resolve_live_python_command("python3", live_py_cache=live_py_cache)
        ):
            return ([], None)
        return ([(cmd_tok.start, cmd_tok.end, f"{py_replacement} {script_literal}")], None)
    return ([], None)


def _inspect_legacy_claude_command(
    raw_command: str,
    *,
    legacy_suffix: str,
    new_script: str,
    py_exe: str,
    powershell: bool,
    live_py_cache: dict[str, bool] | None = None,
) -> tuple[str | None, str | None]:
    """Upgrade certified executed legacy commands and report uncertifiable dynamic legacy references."""
    prefilter_command = raw_command.replace("\\\n", "") if not powershell else raw_command
    if legacy_suffix not in prefilter_command.replace("\\", "/"):
        return (None, None)
    tokens = _tokenize_shell_spans(raw_command, powershell=powershell)
    if tokens is None:
        return (None, "unterminated shell quote or here-document around legacy Claude command")
    if not tokens:
        return (None, None)

    edits: list[tuple[int, int, str]] = []
    manual_reasons: list[str] = []
    for words, has_ps_call in _split_command_segments(
        tokens, raw_command=raw_command, powershell=powershell
    ):
        seg_edits, seg_reason = _segment_edits(
            words,
            has_ps_call=has_ps_call,
            raw_command=raw_command,
            legacy_suffix=legacy_suffix,
            new_script=new_script,
            py_exe=py_exe,
            powershell=powershell,
            live_py_cache=live_py_cache,
        )
        edits.extend(seg_edits)
        if seg_reason and seg_reason not in manual_reasons:
            manual_reasons.append(seg_reason)
    manual_reason = "; ".join(manual_reasons) if manual_reasons else None
    if not edits:
        return (None, manual_reason)

    edits.sort(key=lambda item: item[0], reverse=True)
    updated = raw_command
    last_start = len(raw_command) + 1
    for start, end, replacement in edits:
        if end > last_start:
            return (None, "overlapping shell spans prevent safe automatic migration")
        updated = updated[:start] + replacement + updated[end:]
        last_start = start
    return (updated if updated != raw_command else None, manual_reason)


def _rewrite_legacy_claude_command(
    raw_command: str,
    *,
    legacy_suffix: str,
    new_script: str,
    py_exe: str,
    powershell: bool,
) -> str | None:
    """Upgrade executed legacy Claude hook/statusline commands while keeping wrappers and data args."""
    updated, _ = _inspect_legacy_claude_command(
        raw_command,
        legacy_suffix=legacy_suffix,
        new_script=new_script,
        py_exe=py_exe,
        powershell=powershell,
    )
    return updated


def _inspect_settings_migration(
    raw_data: dict[str, Any],
    *,
    py_exe: str,
    live_py_cache: dict[str, bool] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    powershell = _uses_powershell()
    gate_script = _p("hooks", "goal_policy.py")
    status_script = _p("statusline", "goal_status.py")
    updated: dict[str, Any] = dict(raw_data)
    changed = False
    manual_reasons: list[str] = []

    hooks_obj = raw_data.get("hooks")
    if isinstance(hooks_obj, dict):
        pre_obj = hooks_obj.get("PreToolUse")
        if isinstance(pre_obj, list):
            new_pre: list[Any] = []
            pre_changed = False
            for group in pre_obj:
                if not isinstance(group, dict):
                    new_pre.append(group)
                    continue
                group_hooks = group.get("hooks")
                if not isinstance(group_hooks, list):
                    new_pre.append(group)
                    continue
                new_group_hooks: list[Any] = []
                group_changed = False
                for item in group_hooks:
                    if isinstance(item, dict) and isinstance(item.get("command"), str):
                        migrated_cmd, manual_reason = _inspect_legacy_claude_command(
                            item["command"],
                            legacy_suffix=_LEGACY_POLICY_HOOK_SUFFIX,
                            new_script=gate_script,
                            py_exe=py_exe,
                            powershell=powershell,
                            live_py_cache=live_py_cache,
                        )
                        if manual_reason and manual_reason not in manual_reasons:
                            manual_reasons.append(manual_reason)
                        if migrated_cmd is not None:
                            new_item = dict(item)
                            new_item["command"] = migrated_cmd
                            new_group_hooks.append(new_item)
                            group_changed = True
                            continue
                    new_group_hooks.append(item)
                if group_changed:
                    new_group = dict(group)
                    new_group["hooks"] = new_group_hooks
                    new_pre.append(new_group)
                    pre_changed = True
                else:
                    new_pre.append(group)
            if pre_changed:
                new_hooks = dict(hooks_obj)
                new_hooks["PreToolUse"] = new_pre
                updated["hooks"] = new_hooks
                changed = True

    sl_obj = raw_data.get("statusLine")
    if isinstance(sl_obj, dict) and isinstance(sl_obj.get("command"), str):
        migrated_sl, sl_reason = _inspect_legacy_claude_command(
            sl_obj["command"],
            legacy_suffix=_LEGACY_STATUSLINE_SCRIPT_SUFFIX,
            new_script=status_script,
            py_exe=py_exe,
            powershell=powershell,
            live_py_cache=live_py_cache,
        )
        if sl_reason and sl_reason not in manual_reasons:
            manual_reasons.append(sl_reason)
        if migrated_sl is not None:
            new_sl = dict(sl_obj)
            new_sl["command"] = migrated_sl
            updated["statusLine"] = new_sl
            changed = True

    return (updated if changed else None, manual_reasons)


def _migrated_settings_payload(raw_data: dict[str, Any], *, py_exe: str) -> dict[str, Any] | None:
    updated, _ = _inspect_settings_migration(raw_data, py_exe=py_exe)
    return updated


def _migrated_mcp_json_payload(
    raw_data: dict[str, Any],
    *,
    live_py_cache: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    servers = raw_data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    loopx_entry = servers.get("loopx")
    if not isinstance(loopx_entry, dict):
        return None
    args = loopx_entry.get("args")
    if not isinstance(args, list):
        return None
    cmd = loopx_entry.get("command")
    live_cmd = isinstance(cmd, str) and _resolve_live_python_command(
        cmd, live_py_cache=live_py_cache
    )
    new_mcp_script = _p("mcp", "loopx_mcp.py")
    updated_args: list[Any] = []
    changed = False
    for arg in args:
        if isinstance(arg, str) and _LEGACY_MCP_SCRIPT_SUFFIX in arg.replace("\\", "/"):
            if live_cmd and _is_live_legacy_shim_path(
                arg, legacy_suffix=_LEGACY_MCP_SCRIPT_SUFFIX
            ):
                updated_args.append(arg)
            else:
                updated_args.append(new_mcp_script)
                changed = True
        else:
            updated_args.append(arg)
    if not changed:
        return None
    updated_entry = dict(loopx_entry)
    updated_entry["args"] = updated_args
    updated_servers = dict(servers)
    updated_servers["loopx"] = updated_entry
    updated = dict(raw_data)
    updated["mcpServers"] = updated_servers
    return updated


def _read_json_dict_fail_closed(path: Path) -> dict[str, Any] | None:
    try:
        raw_obj: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return raw_obj if isinstance(raw_obj, dict) else None


def migrate_legacy_claude_dir(
    claude_dir: Path,
    *,
    project_dir: Path | None = None,
    execute: bool,
) -> list[dict[str, str]]:
    """Migrate provably LoopX-owned legacy `loopx/claude_goal_mode/` entries in `claude_dir`.

    Leaves user-owned rules/hooks/statusline/commands and unreadable JSON/non-UTF-8 files untouched.
    """
    results: list[dict[str, str]] = []
    py_exe: str | None = None
    live_py_cache: dict[str, bool] = {}

    def resolve_py() -> str:
        nonlocal py_exe
        if py_exe is None:
            py_exe = _migration_python_cmd()
        return py_exe

    cmd_path = claude_dir / "commands" / "loopx.md"
    if cmd_path.is_file():
        try:
            raw_cmd = cmd_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raw_cmd = ""
        updated_cmd = (
            _migrated_command_md_text(
                raw_cmd, py_exe=resolve_py(), live_py_cache=live_py_cache
            )
            if raw_cmd
            else None
        )
        if updated_cmd is not None:
            if execute:
                write_bytes_atomic(
                    cmd_path.resolve(),
                    updated_cmd.encode("utf-8"),
                    preserve_mode=True,
                )
            results.append({"path": str(cmd_path), "status": "updated" if execute else "would_write"})

    settings_path = claude_dir / "settings.json"
    if settings_path.is_file():
        raw_settings = _read_json_dict_fail_closed(settings_path)
        updated_settings, manual_reasons = (
            _inspect_settings_migration(
                raw_settings, py_exe=resolve_py(), live_py_cache=live_py_cache
            )
            if raw_settings is not None
            else (None, [])
        )
        if updated_settings is not None:
            if execute:
                write_json_atomic(
                    settings_path.resolve(),
                    updated_settings,
                    preserve_mode=True,
                )
            results.append({"path": str(settings_path), "status": "updated" if execute else "would_write"})
        if manual_reasons:
            scope_args = (
                f"--scope project --project {shlex.quote(str(project_dir))} --harden"
                if project_dir is not None
                else "--scope user --harden"
            )
            recovery_cmd = f"{_shell_literal(resolve_py(), powershell=_uses_powershell())} {_shell_literal(str(Path(__file__).resolve()), powershell=_uses_powershell())} {scope_args}"
            results.append(
                {
                    "path": str(settings_path),
                    "status": "manual_migration_required",
                    "reason": "; ".join(manual_reasons),
                    "recovery": recovery_cmd,
                    "note": (
                        f"Manual Claude migration required for {settings_path} "
                        f"({'; '.join(manual_reasons)}). Recover with: {recovery_cmd}"
                    ),
                }
            )

    if project_dir is not None:
        mcp_path = project_dir / ".mcp.json"
        if mcp_path.is_file():
            raw_mcp = _read_json_dict_fail_closed(mcp_path)
            updated_mcp = (
                _migrated_mcp_json_payload(raw_mcp, live_py_cache=live_py_cache)
                if raw_mcp is not None
                else None
            )
            if updated_mcp is not None:
                if execute:
                    write_json_atomic(
                        mcp_path.resolve(),
                        updated_mcp,
                        preserve_mode=True,
                    )
                results.append({"path": str(mcp_path), "status": "updated" if execute else "would_write"})

    return results


if __name__ == "__main__":
    raise SystemExit(main())
