"""An unanswered Git read keeps its own answer; an older release.json is another install."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from loopx import doctor_git
from loopx.release_manifest import write_release_manifest

STALE_COMMIT = "a" * 40


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    repo.mkdir()
    for command in (
        ["git", "init", "-q", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        ["git", "-C", str(repo), "config", "user.name", "Source Fixture"],
    ):
        subprocess.run(command, check=True)
    (repo / "tracked.txt").write_text("current\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "current"], check=True)
    return repo


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, encoding="utf-8",
    ).stdout.strip()


def _source_manifest(release_root: Path, source_root: Path) -> dict[str, object]:
    release_root.mkdir()
    path = write_release_manifest(
        release_root=release_root, release_id="fixture", source_root=source_root, env={},
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest["source"]


def test_a_stale_manifest_does_not_stand_in_for_an_unanswered_root_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _checkout(tmp_path)
    head = _head(repo)
    (repo / "release.json").write_text(
        json.dumps({
            "source": {
                "kind": "github_archive",
                "git_commit": STALE_COMMIT,
                "git_ref": "v0.9.0",
                "git_dirty": False,
                "ref": "v0.9.0",
            }
        }),
        encoding="utf-8",
    )

    healthy = _source_manifest(tmp_path / "release-healthy", repo)
    assert healthy["git_commit"] == head
    assert healthy["git_ref"] is not None

    real_git = shutil.which("git")
    assert real_git is not None
    slow_bin = tmp_path / "slow-bin"
    slow_bin.mkdir()
    wrapper = slow_bin / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        '  if [ "$argument" = "--show-toplevel" ]; then sleep 30; fi\n'
        "done\n"
        f'exec {shlex_quote(real_git)} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{slow_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(doctor_git, "GIT_PROBE_TIMEOUT_SECONDS", 0.5)

    delayed = _source_manifest(tmp_path / "release-delayed", repo)

    assert delayed["git_commit"] != STALE_COMMIT
    assert delayed["git_commit"] is None
    assert delayed["git_dirty"] is None
    assert delayed["kind"] == "local_checkout"


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
