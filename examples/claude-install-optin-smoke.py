#!/usr/bin/env python3
"""Smoke-test the Claude Code adapter install boundary.

Proves the install-side review requirements:
  1. the normal loopx install (`scripts/install-local.sh`) may install
     lightweight Claude Code slash-command skills, but does NOT install the
     Claude adapter command/settings/hooks unless the adapter is explicitly
     opted in;
  2. `install.py` requires a deliberate `--scope user|project` and confines
     project-scope writes to that project (no global side effect); hooks are
     opt-in via `--harden`, not the default;
  3. install never mutates existing Claude permission rules — a legacy loopx
     `deny` rule is preserved and a manual-cleanup note is printed instead.

`--skip-mcp` keeps the install.py checks fast and offline (no venv / pip / claude).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install-local.sh"
INSTALLER = REPO_ROOT / "loopx" / "claude_goal_mode" / "scripts" / "install.py"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _install_py(args, **kw):
    return _run([sys.executable, str(INSTALLER), *args], **kw)


def _isolated_host_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", os.defpath),
        "SHELL": "/bin/zsh",
        # Rebuilding the environment must not restore telemetry in synthetic runs.
        "LOOPX_USAGE_PING": "0",
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_HOME": str(home / ".claude"),
        "OPENCODE_CONFIG_DIR": str(home / ".config" / "opencode"),
    }


def main() -> int:
    # --- 1) default install only writes lightweight slash skills --------------
    with tempfile.TemporaryDirectory(prefix="loopx-claude-optin-smoke-") as tmp:
        home = Path(tmp) / "home"
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        outside_host_root = Path(tmp) / "outside-host-root"
        outside_host_root.mkdir()
        sentinel = outside_host_root / "keep.txt"
        sentinel.write_text("outside fixture must stay unchanged\n", encoding="utf-8")
        inherited_roots = {
            "CODEX_HOME": str(outside_host_root / "codex"),
            "CLAUDE_HOME": str(outside_host_root / "claude"),
            "OPENCODE_CONFIG_DIR": str(outside_host_root / "opencode"),
            "LOOPX_SKILLS_DIR": str(outside_host_root / "skills"),
            "LOOPX_RELEASES_DIR": str(outside_host_root / "releases"),
            "LOOPX_MAN_DIR": str(outside_host_root / "man"),
            "LOOPX_RUNTIME_ROOT": str(outside_host_root / "runtime"),
        }
        with patch.dict(os.environ, inherited_roots):
            env = {
                **_isolated_host_env(home),
                "LOOPX_BIN_DIR": str(bin_dir),
                "LOOPX_SHELL_PROFILE": str(home / ".zshrc"),
                "LOOPX_INSTALL_CANARY": "0",
                "LOOPX_INSTALL_SKILL": "0",
                "LOOPX_PROMOTE_DEFAULT": "1",
                "LOOPX_PYTHON": sys.executable,
            }
            r = _run(["bash", str(INSTALL_SH)], env=env, cwd=str(REPO_ROOT), timeout=240)
        assert r.returncode == 0, f"install-local.sh failed:\n{r.stdout}\n{r.stderr}"
        assert (home / ".codex" / "skills" / "loopx" / "SKILL.md").is_file(), (
            "default install should register the lightweight Codex /loopx skill inside the fixture"
        )
        assert (home / ".claude" / "skills" / "loopx" / "SKILL.md").is_file(), (
            "default install should register the lightweight /loopx slash skill"
        )
        assert [path.name for path in outside_host_root.iterdir()] == [sentinel.name], (
            "default install must not write to inherited host roots outside the fixture"
        )
        assert sentinel.read_text(encoding="utf-8") == "outside fixture must stay unchanged\n"
        assert not (home / ".claude" / "commands" / "loopx.md").exists(), (
            "default install must not install the Claude adapter command"
        )
        assert not (home / ".claude" / "settings.json").exists(), (
            "default install must not write Claude adapter hooks/settings"
        )
        assert "skipped (opt-in" in r.stdout, f"summary should mark the adapter opt-in:\n{r.stdout}"

    # --- 2) install.py requires an explicit scope; project scope is isolated ---
    r = _install_py(["--skip-mcp"])  # no --scope
    assert r.returncode != 0, "install.py must require an explicit --scope"

    with tempfile.TemporaryDirectory(prefix="loopx-proj-") as d:
        proj, other_home = Path(d) / "proj", Path(d) / "home"
        proj.mkdir()
        other_home.mkdir()
        env = _isolated_host_env(other_home)  # prove it doesn't touch ~/.claude
        # dry-run writes nothing
        r = _install_py(["--scope", "project", "--project", str(proj), "--skip-mcp", "--dry-run"], env=env)
        assert r.returncode == 0, f"dry-run failed:\n{r.stdout}\n{r.stderr}"
        assert not (proj / ".claude").exists(), "dry-run must not write project .claude"
        # real project install: only the /loopx command, no hooks, nothing global
        r = _install_py(["--scope", "project", "--project", str(proj), "--skip-mcp"], env=env)
        assert r.returncode == 0, f"project install failed:\n{r.stdout}\n{r.stderr}"
        assert (proj / ".claude" / "commands" / "loopx.md").is_file(), "project /loopx command not written"
        assert not (proj / ".claude" / "settings.json").exists(), "default install must not write hooks (settings.json)"
        assert not (other_home / ".claude").exists(), "project-scope install must not touch ~/.claude"

    # --- 2b) hooks are opt-in (--harden) and project-scoped --------------------
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        proj = Path(d)
        r = _install_py(["--scope", "project", "--project", str(proj), "--harden", "--skip-mcp"])
        assert r.returncode == 0, f"--harden failed:\n{r.stdout}\n{r.stderr}"
        cfg = json.loads((proj / ".claude" / "settings.json").read_text())
        cmds = [h.get("command", "") for g in cfg.get("hooks", {}).get("PreToolUse", []) for h in g.get("hooks", [])]
        assert any("goal_policy.py" in c for c in cmds), "--harden should install the PreToolUse should_run gate"

    # --- 3) install never mutates existing permission rules -------------------
    with tempfile.TemporaryDirectory(prefix="loopx-perms-") as d:
        home = Path(d)
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        seed = {
            "permissions": {"deny": ["Read(~/.ssh/**)", "Read(~/.aws/**)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo user-owned-hook"}]}]},
        }
        settings.write_text(json.dumps(seed), encoding="utf-8")
        env = _isolated_host_env(home)
        # default (no --harden): does not touch settings.json, prints a suggestion
        r = _install_py(["--scope", "user", "--skip-mcp"], env=env)
        assert r.returncode == 0, f"user install failed:\n{r.stdout}\n{r.stderr}"
        after = json.loads(settings.read_text())
        assert after == seed, "user-owned permission rules / hooks must be untouched"
        assert "Read(~/.ssh/**)" in r.stdout and "NOT remove" in r.stdout, "should print a manual-cleanup suggestion, not delete"
        # --harden: merges our gate but still preserves the user's deny + hook
        r = _install_py(["--scope", "user", "--harden", "--skip-mcp"], env=env)
        assert r.returncode == 0, f"user --harden failed:\n{r.stdout}\n{r.stderr}"
        after = json.loads(settings.read_text())
        assert after["permissions"]["deny"] == seed["permissions"]["deny"], "--harden must not delete deny rules"
        cmds = [h.get("command", "") for g in after.get("hooks", {}).get("PreToolUse", []) for h in g.get("hooks", [])]
        assert any("echo user-owned-hook" in c for c in cmds), "--harden must preserve the user's own hook"
        assert any("goal_policy.py" in c for c in cmds), "--harden should add the should_run gate"

    print("claude-install-optin-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
