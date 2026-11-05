from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx import contract


def _write_fake_git(directory: Path, script: str) -> None:
    git = directory / "git"
    git.write_text(script, encoding="utf-8")
    git.chmod(0o755)


def test_ownership_questions_degrade_when_git_runs_out_of_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked Git leaves ownership unknown instead of raising out of a scan."""

    target = tmp_path / "public.md"
    target.write_text("public", encoding="utf-8")

    def fake_run(command, **kwargs):
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, str(tmp_path), "")
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(contract.subprocess, "run", fake_run)
    observed = contract._git_probe(target)

    # 超时后既不能说它被跟踪，也不能说它被忽略：两条都落到最保守的答案。
    assert observed == {
        "inside_worktree": True,
        "root": str(tmp_path),
        "tracked": False,
        "ignored": False,
    }


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX shell script")
def test_probe_and_scan_finish_when_git_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Git that never returns must not hold the read-only scan open."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_git(bin_dir, f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    scan_root = tmp_path / "public"
    scan_root.mkdir()
    (scan_root / "notes.md").write_text("public text", encoding="utf-8")

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(contract, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    assert contract._git_worktree_root(scan_root) is None
    assert contract._git_query_succeeds("-C", str(scan_root), "ls-files") is False
    result = contract.scan_public_boundary([scan_root])
    elapsed = time.monotonic() - started

    assert result["files"] >= 1
    assert elapsed < 20, f"a blocked Git held the scan for {elapsed:.1f}s"
