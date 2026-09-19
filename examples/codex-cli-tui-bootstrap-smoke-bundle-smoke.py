#!/usr/bin/env python3
"""Smoke-test the transcript-free Codex CLI TUI bootstrap bundle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "public-fresh-codex-cli-goal"
AGENT_ID = "codex-side-bypass"


def add_tree(tar: tarfile.TarFile, root: Path, name: str) -> None:
    path = root / name
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if ".git" in child.parts or "__pycache__" in child.parts:
                continue
            tar.add(
                child,
                arcname=str(Path("loopx-main") / child.relative_to(root)),
                recursive=False,
            )
    else:
        tar.add(path, arcname=str(Path("loopx-main") / name))


def run(command: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        env=env,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


def path_without_loopx(path_text: str) -> str:
    entries: list[str] = []
    for entry in path_text.split(os.pathsep):
        if not entry:
            continue
        if (Path(entry).expanduser() / "loopx").exists():
            continue
        entries.append(entry)
    return os.pathsep.join(entries)


def main() -> None:
    install_script = REPO_ROOT / "scripts" / "install-from-github.sh"
    subprocess.run(["bash", "-n", str(install_script)], check=True)

    with tempfile.TemporaryDirectory(prefix="loopx-codex-cli-tui-bundle-") as td:
        tmp = Path(td)
        archive = tmp / "loopx.tar.gz"
        home = tmp / "home"
        fake_bin = tmp / "fake-bin"
        fresh_repo = tmp / "public-fresh-repo"
        codex_called_marker = tmp / "codex-called"
        home.mkdir()
        fake_bin.mkdir()
        fresh_repo.mkdir()
        (fresh_repo / "README.md").write_text("# Public fresh repo\n", encoding="utf-8")

        fake_codex = fake_bin / "codex"
        fake_codex.write_text(
            "#!/usr/bin/env bash\n"
            "echo called > \"${CODEX_CALLED_MARKER:?}\"\n"
            "exit 77\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        with tarfile.open(archive, "w:gz") as tar:
            for name in (
                "loopx",
                "scripts",
                "skills",
                "docs",
                "examples",
                "README.md",
                "pyproject.toml",
                "LICENSE",
            ):
                add_tree(tar, REPO_ROOT, name)

        env = os.environ.copy()
        host_path_without_loopx = path_without_loopx(env.get("PATH", ""))
        env.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(home / ".codex"),
                "LOOPX_BIN_DIR": str(home / ".local" / "bin"),
                "LOOPX_RELEASES_DIR": str(home / ".local" / "share" / "loopx" / "releases"),
                "LOOPX_SHELL_PROFILE": str(home / ".profile"),
                "LOOPX_ARCHIVE_URL": f"file://{archive}",
                "LOOPX_INSTALL_CANARY": "0",
                "LOOPX_PYTHON": sys.executable,
                "CODEX_CALLED_MARKER": str(codex_called_marker),
                "PATH": f"{fake_bin}:{host_path_without_loopx}",
            }
        )
        run(["bash", str(install_script)], env=env, cwd=tmp)

        installed = home / ".local" / "bin" / "loopx"
        assert installed.exists(), installed
        run([str(installed), "doctor"], env=env, cwd=fresh_repo)

        bundle = json.loads(
            run(
                [
                    str(installed),
                    "--format",
                    "json",
                    "codex-cli-tui-bootstrap-smoke-bundle",
                    "--project",
                    str(fresh_repo),
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENT_ID,
                ],
                env=env,
                cwd=fresh_repo,
            ).stdout
        )
        assert bundle["schema_version"] == "codex_cli_tui_bootstrap_smoke_bundle_v0", bundle
        assert bundle["goal_id"] == GOAL_ID, bundle
        assert bundle["agent_id"] == AGENT_ID, bundle
        assert "--message-only" in bundle["message_only_command"], bundle
        assert str(fresh_repo) in bundle["message_only_command"], bundle
        assert "loopx-project.github.io/loopx/install.sh" in bundle["install_repair_command"], bundle
        assert "python3 -m pip install --upgrade loopx" in bundle["install_repair_command"], bundle
        assert "workflow-skills --install" in bundle["install_repair_command"], bundle
        assert "quota should-run" in bundle["quota_guard_command"], bundle
        assert "--agent-id codex-side-bypass" in bundle["quota_guard_command"], bundle
        assert "refresh-state --goal-id public-fresh-codex-cli-goal" in bundle["refresh_command"], bundle
        assert "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>" in bundle["progress_refresh_command"], bundle
        assert "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>" in bundle["progress_refresh_command"], bundle
        assert "--delivery-outcome outcome_progress" not in bundle["progress_refresh_command"], bundle
        assert "--agent-id codex-side-bypass" in bundle["progress_refresh_command"], bundle
        assert "quota spend-slot" in bundle["quota_spend_command"], bundle
        assert "--source heartbeat --execute --agent-id codex-side-bypass" in bundle["quota_spend_command"], bundle
        assert any("message-only command" in item for item in bundle["validation_checklist"]), bundle
        assert any("no raw Codex transcripts" in item for item in bundle["validation_checklist"]), bundle

        boundary = bundle["boundary"]
        for key in (
            "runs_codex",
            "reads_raw_transcripts",
            "reads_session_files",
            "reads_credentials",
            "mutates_codex_session",
            "spends_loopx_quota",
            "requires_loopx_repo_clone",
        ):
            assert boundary[key] is False, (key, boundary)

        message_only = run(
            [
                str(installed),
                "codex-cli-bootstrap-message",
                "--project",
                str(fresh_repo),
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
                "--message-only",
            ],
            env=env,
            cwd=fresh_repo,
        ).stdout
        assert message_only.startswith("Install and connect LoopX for this repo"), message_only
        assert not message_only.startswith("/goal "), message_only
        assert "# Codex CLI LoopX Bootstrap Message" not in message_only, message_only
        assert "Fresh Repo Install Repair" not in message_only, message_only
        assert "loopx-project.github.io/loopx/install.sh" in message_only, message_only
        assert "python3 -m pip install --upgrade loopx" in message_only, message_only
        assert "/goal <thin task_body>" in message_only, message_only
        assert "quota should-run" in message_only, message_only
        assert "refresh-state --goal-id public-fresh-codex-cli-goal" in message_only, message_only
        assert "quota spend-slot" in message_only, message_only
        assert "raw Codex transcripts" in message_only, message_only

        markdown = run(
            [
                str(installed),
                "codex-cli-tui-bootstrap-smoke-bundle",
                "--project",
                str(fresh_repo),
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
            ],
            env=env,
            cwd=fresh_repo,
        ).stdout
        assert "# Codex CLI TUI Bootstrap Smoke Bundle" in markdown, markdown
        assert "Transcript-Free Boundary" in markdown, markdown
        assert "runs_codex: `False`" in markdown, markdown
        assert "requires_loopx_repo_clone: `False`" in markdown, markdown

        assert not codex_called_marker.exists(), codex_called_marker

    print("codex-cli-tui-bootstrap-smoke-bundle-smoke ok")


if __name__ == "__main__":
    main()
