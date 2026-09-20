#!/usr/bin/env python3
"""Smoke-test the public fresh-clone quickstart path.

This intentionally starts from a git clone and an isolated HOME so the README
install path proves more than the current developer checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "fresh-clone-quickstart-smoke-goal"


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def run_loopx(
    *args: str,
    cwd: Path,
    env: dict[str, str],
) -> dict:
    result = run(["loopx", "--format", "json", *args], cwd=cwd, env=env)
    return json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-fresh-clone-smoke-") as tmp:
        root = Path(tmp)
        clone = root / "loopx"
        run(
            ["git", "clone", "--quiet", "--no-local", str(REPO_ROOT), str(clone)],
            cwd=root,
            env=os.environ.copy(),
        )

        home = root / "home"
        bin_dir = home / ".local" / "bin"
        man_root = home / ".local" / "share" / "man"
        codex_home = home / ".codex"
        profile = home / ".zshrc"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "LOOPX_BIN_DIR": str(bin_dir),
            "LOOPX_SHELL_PROFILE": str(profile),
            "LOOPX_INSTALL_SKILL": "1",
            "LOOPX_INSTALL_CANARY": "1",
            "LOOPX_PYTHON": sys.executable,
            "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_RELEASE_ID": "fresh-clone-smoke-release",
            "PATH": os.environ.get("PATH", ""),
            "SHELL": "/bin/zsh",
        }

        install = run([str(clone / "scripts" / "install-local.sh")], cwd=clone, env=env)
        assert "loopx installed locally" in install.stdout, install.stdout
        assert f"- executable: {bin_dir / 'loopx'}" in install.stdout, install.stdout
        assert f"- manpage: {man_root / 'man1' / 'loopx.1.gz'}" in install.stdout, install.stdout
        assert f"- canary executable: {bin_dir / 'loopx-canary'}" in install.stdout, install.stdout
        assert f"- skill: {codex_home / 'skills' / 'loopx-project'}" in install.stdout, install.stdout
        assert f"- skill: {codex_home / 'skills' / 'loopx-pr-program'}" in install.stdout, install.stdout
        assert f"- skill: {codex_home / 'skills' / 'loopx-pr-review'}" in install.stdout, install.stdout
        assert "promotion-readiness evidence is missing" in install.stderr, install.stderr
        assert "non-blocking" in install.stderr, install.stderr

        wrapper = bin_dir / "loopx"
        canary_wrapper = bin_dir / "loopx-canary"
        assert wrapper.is_symlink(), wrapper
        assert not (bin_dir / "goal-harness").exists()
        assert canary_wrapper.is_symlink(), canary_wrapper
        assert not (bin_dir / "goal-harness-canary").exists()
        assert canary_wrapper.resolve() == clone.resolve() / "scripts" / "loopx", canary_wrapper.resolve()
        release_root = wrapper.resolve().parents[1]
        assert release_root != clone, release_root
        assert (release_root / "loopx" / "cli.py").is_file(), release_root
        assert (man_root / "man1" / "loopx.1.gz").is_file()
        assert 'export MANPATH="$HOME/.local/share/man:${MANPATH:-}"' in profile.read_text(
            encoding="utf-8"
        )
        assert (codex_home / "skills" / "loopx-project" / "SKILL.md").is_file()
        assert (codex_home / "skills" / "loopx-pr-program" / "SKILL.md").is_file()
        assert (codex_home / "skills" / "loopx-pr-review" / "SKILL.md").is_file()
        assert (codex_home / "skills" / "loopx-self-repair" / "SKILL.md").is_file()

        cli_env = {**env, "PATH": f"{bin_dir}:{env['PATH']}"}
        doctor = run_loopx("doctor", cwd=root, env=cli_env)
        assert doctor["ok"] is True, doctor
        assert doctor["path"]["loopx"] == str(wrapper), doctor
        assert doctor["path"]["loopx_canary"] == str(canary_wrapper), doctor
        assert doctor["package"]["release_root"] == str(release_root), doctor
        assert doctor["install_freshness"]["schema_version"] == "loopx_install_freshness_v0", doctor
        assert doctor["install_freshness"]["status"] == "unknown", doctor
        assert "loopx-project.github.io/loopx/install.sh" in doctor["install_freshness"]["upgrade_command"], doctor
        assert doctor["skills"]["loopx-project"]["exists"] is True, doctor
        assert doctor["skills"]["loopx-pr-program"]["exists"] is True, doctor
        assert doctor["skills"]["loopx-pr-review"]["exists"] is True, doctor
        assert doctor["skills"]["loopx-self-repair"]["exists"] is True, doctor

        project = root / "sample-project"
        project.mkdir()
        (project / "README.md").write_text(
            "# Sample Project\n\nUse LoopX to coordinate this sample goal.\n",
            encoding="utf-8",
        )
        bootstrap = run_loopx(
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Keep a sample project coordinated with LoopX.",
            "--domain",
            "fresh-clone-smoke",
            "--goal-doc",
            "README.md",
            "--no-global-sync",
            cwd=project,
            env=cli_env,
        )
        assert bootstrap["ok"] is True, bootstrap
        assert bootstrap["goal_id"] == GOAL_ID, bootstrap
        assert "python3 -m pip install --upgrade loopx" in bootstrap["install_repair_command"], bootstrap
        assert "loopx workflow-skills --install" in bootstrap["install_repair_command"], bootstrap
        assert "loopx-project.github.io/loopx/install.sh" in bootstrap["archive_fallback_install_command"], bootstrap
        assert "loopx doctor" in bootstrap["install_repair_command"], bootstrap
        assert (project / ".loopx" / "registry.json").is_file(), bootstrap
        assert (project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md").is_file(), bootstrap

        registry = project / ".loopx" / "registry.json"
        status = run_loopx("--registry", str(registry), "status", cwd=project, env=cli_env)
        assert status["ok"] is True, status
        assert status["status_contract"]["minimum_dashboard_schema_version"] >= 2, status
        assert status["attention_queue"]["item_count"] == 1, status
        assert status["attention_queue"]["items"][0]["goal_id"] == GOAL_ID, status

        check = run_loopx(
            "--registry",
            str(registry),
            "check",
            "--scan-root",
            str(project),
            cwd=project,
            env=cli_env,
        )
        assert check["ok"] is True, check
        assert check["summary"]["errors"] == 0, check

        heartbeat = run_loopx(
            "--registry",
            str(registry),
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            cwd=project,
            env=cli_env,
        )
        assert heartbeat["ok"] is True, heartbeat
        # The default heartbeat JSON is the thin Agent-input projection: the
        # current task body carries the guard, while settlement commands come
        # from the successful interaction contract and are intentionally not
        # duplicated as stale top-level fields.
        assert heartbeat["schema_version"] == "heartbeat_agent_input_v1", heartbeat
        assert "quota should-run" in heartbeat["task_body"], heartbeat
        assert "quota_guard_command" not in heartbeat, heartbeat
        assert "quota_spend_command" not in heartbeat, heartbeat

    print("fresh-clone-quickstart-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
