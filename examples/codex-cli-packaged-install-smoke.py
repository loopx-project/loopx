#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def main() -> None:
    script = REPO_ROOT / "scripts" / "install-from-github.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        archive = tmp / "loopx.tar.gz"
        home = tmp / "home"
        fake_bin = tmp / "fake-bin"
        unexpected_python_marker = tmp / "unexpected-python3"
        home.mkdir()
        fake_bin.mkdir()
        fake_python = fake_bin / "python3"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            f"touch {unexpected_python_marker!s}\n"
            "exit 86\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

        with tarfile.open(archive, "w:gz") as tar:
            for name in (
                "loopx",
                "scripts",
                "skills",
                "docs",
                "man",
                "examples",
                "README.md",
                "pyproject.toml",
                "LICENSE",
            ):
                add_tree(tar, REPO_ROOT, name)

        env = os.environ.copy()
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
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            }
        )
        subprocess.run(["bash", str(script)], check=True, env=env, cwd=tmp)
        assert not unexpected_python_marker.exists(), unexpected_python_marker

        installed = home / ".local" / "bin" / "loopx"
        assert installed.exists(), installed
        assert (home / ".local" / "share" / "man" / "man1" / "loopx.1.gz").is_file()
        assert not (home / ".local" / "bin" / "goal-harness").exists()
        doctor = subprocess.run(
            [str(installed), "doctor"],
            check=True,
            env={**env, "PATH": f"{home / '.local' / 'bin'}:{env.get('PATH', '')}"},
            text=True,
            capture_output=True,
        )
        assert "ok: `True`" in doctor.stdout, doctor.stdout
        assert "## Install Freshness" in doctor.stdout, doctor.stdout
        assert "release_manifest_available: `True`" in doctor.stdout, doctor.stdout
        assert "loopx-project.github.io/loopx/install.sh" in doctor.stdout, doctor.stdout
        doctor_json = subprocess.run(
            [str(installed), "--format", "json", "doctor"],
            check=True,
            env={**env, "PATH": f"{home / '.local' / 'bin'}:{env.get('PATH', '')}"},
            text=True,
            capture_output=True,
        )
        doctor_payload = json.loads(doctor_json.stdout)
        release_root = Path(doctor_payload["package"]["release_root"])
        manifest_path = release_root / "release.json"
        assert manifest_path.is_file(), manifest_path
        manifest = doctor_payload["release_manifest"]["manifest"]
        assert manifest["source"]["promotion_mode"] == "trusted_github_archive", manifest
        assert manifest["schema_version"] == "loopx_release_manifest_v0", manifest
        assert manifest["source"]["kind"] == "github_archive", manifest
        assert manifest["source"]["repo"] == "loopx-project/loopx", manifest
        assert manifest["source"]["ref"] == "stable", manifest
        assert manifest["source"]["archive_url"] == f"file://{archive}", manifest
        assert manifest["source"]["archive_sha256"], manifest
        assert manifest["skills"]["digest"], manifest

        for skill in (
            "loopx-project",
            "loopx-pr-program",
            "loopx-pr-review",
            "loopx-doc-registry",
            "loopx-benchmark",
            "loopx-self-repair",
        ):
            assert (home / ".codex" / "skills" / skill / "SKILL.md").exists(), skill
        assert not (
            home / ".codex" / "skills" / "loopx-change-quality"
        ).exists()

        assert not (home / ".local" / "bin" / "loopx-canary").exists()
        assert not (home / ".local" / "bin" / "goal-harness-canary").exists()


if __name__ == "__main__":
    main()
