from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_source_launcher_uses_project_venv_without_path_activation(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    scripts = release_root / "scripts"
    scripts.mkdir(parents=True)
    package = release_root / "loopx"
    package.mkdir()
    (package / "cli.py").write_text("print('launcher selected the project interpreter')\n", encoding="utf-8")
    shutil.copy2(REPO_ROOT / "scripts/loopx", scripts / "loopx")
    shutil.copy2(REPO_ROOT / "scripts/loopx-python.sh", scripts / "loopx-python.sh")

    selected = tmp_path / "selected-python.log"
    _write_stub(
        release_root / ".venv/bin/python",
        "printf selected > "
        f"{shlex.quote(str(selected))}\n"
        f"exec {shlex.quote(sys.executable)} \"$@\"",
    )

    completed = subprocess.run(
        ["bash", str(scripts / "loopx"), "version"],
        cwd=release_root,
        env={
            **{key: value for key, value in os.environ.items() if key != "LOOPX_PYTHON"},
            "PATH": "/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert selected.read_text(encoding="utf-8") == "selected"
    assert "Python 3.11+ is required" not in completed.stderr


def test_source_launcher_preserves_explicit_missing_python_diagnostic(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    scripts = release_root / "scripts"
    scripts.mkdir(parents=True)
    (release_root / "loopx").mkdir()
    shutil.copy2(REPO_ROOT / "scripts/loopx", scripts / "loopx")
    shutil.copy2(REPO_ROOT / "scripts/loopx-python.sh", scripts / "loopx-python.sh")

    missing = tmp_path / "missing-python"
    completed = subprocess.run(
        ["bash", str(scripts / "loopx"), "version"],
        cwd=release_root,
        env={
            **{key: value for key, value in os.environ.items() if key != "LOOPX_PYTHON"},
            "LOOPX_PYTHON": str(missing),
            "PATH": "/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "configured Python executable not found" in completed.stderr
    assert str(missing) in completed.stderr
