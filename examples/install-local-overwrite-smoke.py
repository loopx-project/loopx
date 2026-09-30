#!/usr/bin/env python3
"""Smoke-test focused local installer overwrite behavior."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-local.sh"


def install_env(root: Path, bin_dir: Path, profile: Path) -> dict[str, str]:
    home = root / "home"
    codex_home = home / ".codex"
    releases_dir = home / ".local" / "share" / "loopx" / "releases"
    return {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "LOOPX_BIN_DIR": str(bin_dir),
        "LOOPX_RELEASES_DIR": str(releases_dir),
        "LOOPX_SHELL_PROFILE": str(profile),
        "LOOPX_INSTALL_CANARY": "0",
        "LOOPX_INSTALL_SKILL": "0",
        "LOOPX_PROMOTE_DEFAULT": "1",
        "LOOPX_PYTHON": sys.executable,
        "PATH": os.environ.get("PATH", ""),
        "SHELL": "/bin/zsh",
    }


def run_install(
    env: dict[str, str],
    release_id: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"install-local-overwrite-smoke: installing {release_id}", flush=True)
    started = time.monotonic()
    result = subprocess.run(
        [str(INSTALL_SCRIPT)],
        cwd=REPO_ROOT,
        env={**env, "LOOPX_RELEASE_ID": release_id},
        check=check,
        capture_output=True,
        text=True,
    )
    print(f"install-local-overwrite-smoke: {release_id} in {time.monotonic() - started:.2f}s", flush=True)
    return result


def assert_loopx_link_points_to(wrapper: Path, release_id: str) -> None:
    assert wrapper.is_symlink(), wrapper
    target = wrapper.resolve()
    assert target.name == "loopx", target
    release_root = target.parents[1]
    assert release_root.name == release_id, target
    assert (release_root / "release.json").is_file(), release_root


def assert_regular_file_and_symlink_overwrite() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-install-overwrite-") as tmp:
        root = Path(tmp)
        home = root / "home"
        bin_dir = home / ".local" / "bin"
        profile = home / ".zshrc"
        bin_dir.mkdir(parents=True)
        wrapper = bin_dir / "loopx"
        wrapper.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        wrapper.chmod(0o755)

        env = install_env(root, bin_dir, profile)
        first = run_install(env, "overwrite-initial")
        assert first.returncode == 0, first.stderr
        assert "loopx installed locally" in first.stdout, first.stdout
        assert_loopx_link_points_to(wrapper, "overwrite-initial")

        second = run_install(env, "overwrite-second")
        assert second.returncode == 0, second.stderr
        assert_loopx_link_points_to(wrapper, "overwrite-second")
        assert wrapper.resolve().parents[1].name != "overwrite-initial", wrapper.resolve()


def assert_directory_is_not_overwritten() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-install-directory-") as tmp:
        root = Path(tmp)
        home = root / "home"
        bin_dir = home / ".local" / "bin"
        profile = home / ".zshrc"
        wrapper = bin_dir / "loopx"
        wrapper.mkdir(parents=True)
        sentinel = wrapper / "user.txt"
        sentinel.write_text("keep user content", encoding="utf-8")

        env = install_env(root, bin_dir, profile)
        failed = run_install(env, "directory-conflict", check=False)
        assert failed.returncode != 0, failed.stdout
        assert wrapper.is_dir(), wrapper
        assert "loopx installer error:" in failed.stderr, failed.stderr
        assert f"{wrapper} is a directory; remove it before installing" in failed.stderr, failed.stderr
        assert sentinel.read_text(encoding="utf-8") == "keep user content"
        assert not (home / ".local" / "share" / "loopx" / "releases").exists()
        assert not (home / ".codex").exists()
        assert not profile.exists()


def main() -> int:
    assert_regular_file_and_symlink_overwrite()
    assert_directory_is_not_overwritten()
    print("install-local-overwrite-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
