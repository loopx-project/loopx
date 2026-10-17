#!/usr/bin/env python3
"""Verify the Codex CLI no-clone first-run release route."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-no-clone-release-verification.md"
FIRST_RUN_DOC = REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-first-run-rehearsal.md"
PRODUCT_README = (
    REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "README.md"
)
GOAL_ID = "public-no-clone-release-goal"
AGENT_ID = "codex-side-bypass"
FIRST_RUN_COMMANDS = (
    "codex-cli-bootstrap-message",
    "codex-cli-tui-bootstrap-smoke-bundle",
    "codex-cli-visible-attach-acceptance",
)


def normalize(text: str) -> str:
    return " ".join(text.split())


def package_version(root: Path) -> str:
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"$',
        (root / "loopx" / "__init__.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"missing LoopX version in {root}")
    return match.group(1)


def run(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        env=env,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


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


def assert_docs() -> None:
    doc = DOC.read_text(encoding="utf-8")
    first_run = FIRST_RUN_DOC.read_text(encoding="utf-8")
    product_readme = PRODUCT_README.read_text(encoding="utf-8")
    normalized = normalize(doc)

    must_have = (
        "Codex CLI No-Clone Release Verification",
        "scripts/install-from-github.sh",
        "codex-cli-bootstrap-message",
        "codex-cli-tui-bootstrap-smoke-bundle",
        "codex-cli-visible-attach-acceptance",
        "Current archive route: **qualified as the recovery fallback, with one boundary**.",
        "network access to GitHub archive endpoints",
        "same-TUI automation is not the default path",
        "python3 examples/release/codex-cli-no-clone-release-verification-smoke.py",
        "no raw Codex transcript or session material",
        "no Codex execution as part of the verifier",
    )
    for phrase in must_have:
        assert phrase in normalized, phrase

    for command in FIRST_RUN_COMMANDS:
        assert command in doc, command
    assert "codex-cli-no-clone-release-verification.md" in first_run, first_run
    assert "codex-cli-no-clone-release-verification.md" in product_readme, product_readme


def assert_installed_release(
    *,
    installed: Path,
    release_dir: Path,
    env: dict[str, str],
    fresh_repo: Path,
    codex_called_marker: Path,
) -> None:
    runtime_env = {
        **env,
        "PATH": f"{installed.parent}:{env.get('PATH', '')}",
    }
    doctor = run([str(installed), "doctor"], env=runtime_env, cwd=fresh_repo)
    assert "ok: `True`" in doctor.stdout, doctor.stdout
    doctor_payload = json.loads(
        run(
            [str(installed), "--format", "json", "doctor"],
            env=runtime_env,
            cwd=fresh_repo,
        ).stdout
    )
    expected_version = package_version(release_dir)
    manifest_package = doctor_payload["release_manifest"]["manifest"]["package"]
    assert manifest_package["version"] == expected_version, manifest_package
    assert manifest_package["version_tag"] == f"v{expected_version}", manifest_package
    assert doctor_payload["install_freshness"]["manifest_package_version_matches_runtime"] is True, doctor_payload

    help_text = run([str(installed), "--help"], env=runtime_env, cwd=fresh_repo).stdout
    assert "loopx commands" in help_text, help_text
    commands_text = run([str(installed), "commands"], env=runtime_env, cwd=fresh_repo).stdout
    for command in FIRST_RUN_COMMANDS:
        assert command in commands_text, commands_text

    assert (release_dir / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-first-run-rehearsal.md").exists()
    assert (release_dir / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-no-clone-release-verification.md").exists()
    fixture_dir = release_dir / "examples" / "fixtures" / "codex-cli-visible-proof"
    assert fixture_dir.exists(), fixture_dir

    message = run(
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
        env=runtime_env,
        cwd=fresh_repo,
    ).stdout
    normalized_message = normalize(message)
    assert message.startswith("Install and connect LoopX for this repo"), message
    assert not message.startswith("/goal "), message
    assert "setup/bootstrap instruction" in normalized_message, message
    assert "/goal <loader task_body>" in normalized_message, message
    assert "python3 -m pip install --upgrade loopx" in normalized_message, message
    assert "workflow-skills --install" in normalized_message, message
    assert "Codex CLI TUI" in normalized_message, message
    assert "quota should-run" in normalized_message, message
    assert "settlement_plan.ordered_steps" in normalized_message, message
    assert "raw Codex transcripts" in normalized_message, message

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
            env=runtime_env,
            cwd=fresh_repo,
        ).stdout
    )
    assert bundle["schema_version"] == "codex_cli_tui_bootstrap_smoke_bundle_v0", bundle
    assert bundle["boundary"]["runs_codex"] is False, bundle
    assert bundle["boundary"]["requires_loopx_repo_clone"] is False, bundle
    assert bundle["boundary"]["spends_loopx_quota"] is False, bundle
    assert str(fresh_repo) in bundle["message_only_command"], bundle

    acceptance = json.loads(
        run(
            [
                str(installed),
                "--format",
                "json",
                "codex-cli-visible-attach-acceptance",
                "--project",
                str(fresh_repo),
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
                "--fixture",
                str(fixture_dir / "codex-visible-resume-help.public.json"),
                "--proof-fixture",
                str(fixture_dir / "visible-resume-proof.public.json"),
                "--idle-fixture",
                str(fixture_dir / "runtime-idle-visible-resume.public.json"),
            ],
            env=runtime_env,
            cwd=fresh_repo,
        ).stdout
    )
    assert acceptance["decision"] == "visible_surface_spike_passed_not_same_tui", acceptance
    assert acceptance["accepted_for_visible_later_turn"] is True, acceptance
    assert acceptance["accepted_for_same_tui_automation"] is False, acceptance
    assert acceptance["boundary"]["runs_codex"] is False, acceptance
    assert acceptance["boundary"]["reads_raw_transcripts"] is False, acceptance
    assert acceptance["boundary"]["reads_session_files"] is False, acceptance

    assert not codex_called_marker.exists(), codex_called_marker


def main() -> None:
    assert_docs()

    install_script = REPO_ROOT / "scripts" / "install-from-github.sh"
    subprocess.run(["bash", "-n", str(install_script)], check=True)

    with tempfile.TemporaryDirectory(prefix="loopx-no-clone-release-") as td:
        tmp = Path(td)
        archive = tmp / "loopx.tar.gz"
        home = tmp / "home"
        fake_bin = tmp / "fake-bin"
        fresh_repo = tmp / "public-fresh-project"
        stale_checkout = tmp / "stale-loopx-checkout"
        releases_dir = home / ".local" / "share" / "loopx" / "releases"
        codex_called_marker = tmp / "codex-called"
        home.mkdir()
        fake_bin.mkdir()
        fresh_repo.mkdir()
        (fresh_repo / "README.md").write_text("# Public fresh project\n", encoding="utf-8")
        shutil.copytree(REPO_ROOT / "loopx", stale_checkout / "loopx")
        stale_init = stale_checkout / "loopx" / "__init__.py"
        stale_init.write_text(
            re.sub(
                r'^__version__\s*=\s*"[^"]+"$',
                '__version__ = "9.9.9"',
                stale_init.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            ),
            encoding="utf-8",
        )

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
        env.pop("PYTHONPATH", None)
        env.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(home / ".codex"),
                "LOOPX_BIN_DIR": str(home / ".local" / "bin"),
                "LOOPX_RELEASES_DIR": str(releases_dir),
                "LOOPX_SHELL_PROFILE": str(home / ".profile"),
                "LOOPX_ARCHIVE_URL": f"file://{archive}",
                "LOOPX_INSTALL_CANARY": "0",
                "LOOPX_PYTHON": sys.executable,
                "CODEX_CALLED_MARKER": str(codex_called_marker),
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            }
        )
        install = run(["bash", str(install_script)], env=env, cwd=stale_checkout)
        assert "loopx installed locally" in install.stdout, install.stdout
        assert "canary executable: skipped" in install.stdout, install.stdout

        installed = home / ".local" / "bin" / "loopx"
        assert installed.exists(), installed
        release_dirs = [path for path in releases_dir.iterdir() if path.is_dir()]
        assert len(release_dirs) == 1, release_dirs

        assert_installed_release(
            installed=installed,
            release_dir=release_dirs[0],
            env=env,
            fresh_repo=fresh_repo,
            codex_called_marker=codex_called_marker,
        )

    print("codex-cli-no-clone-release-verification-smoke ok")


if __name__ == "__main__":
    main()
