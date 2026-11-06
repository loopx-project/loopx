from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx import registry as registry_module


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_registry_git_probe_skips_git_when_discovery_has_no_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{}\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def unexpected_git(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 128, "", "not a git repository")

    monkeypatch.setattr(registry_module.subprocess, "Popen", unexpected_git)

    assert registry_module._registry_git_probe(registry_path) == {
        "available": True,
        "probe_status": "ok",
        "inside_worktree": False,
        "tracked": False,
        "ignored": False,
        "worktree_root_recorded": False,
    }
    assert calls == []


def test_registry_git_probe_preserves_file_classification_inside_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text("ignored.json\n", encoding="utf-8")
    tracked = repo / "tracked.json"
    ignored = repo / "ignored.json"
    untracked = repo / "untracked.json"
    for path in (tracked, ignored, untracked):
        path.write_text("{}\n", encoding="utf-8")
    run_git(repo, "add", "tracked.json")

    assert registry_module._registry_git_probe(tracked) == {
        "available": True,
        "probe_status": "ok",
        "inside_worktree": True,
        "tracked": True,
        "ignored": False,
        "worktree_root_recorded": False,
    }
    assert registry_module._registry_git_probe(ignored) == {
        "available": True,
        "probe_status": "ok",
        "inside_worktree": True,
        "tracked": False,
        "ignored": True,
        "worktree_root_recorded": False,
    }
    assert registry_module._registry_git_probe(untracked) == {
        "available": True,
        "probe_status": "ok",
        "inside_worktree": True,
        "tracked": False,
        "ignored": False,
        "worktree_root_recorded": False,
    }


def test_registry_git_probe_reports_unavailable_without_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(registry_module.shutil, "which", lambda _: None)

    def unexpected_git(*_: object, **__: object) -> None:
        raise AssertionError("non-Git fast path must not launch Git")

    monkeypatch.setattr(registry_module.subprocess, "Popen", unexpected_git)

    assert registry_module._registry_git_probe(registry_path) == {
        "available": False,
        "probe_status": "git_unavailable",
        "inside_worktree": False,
        "tracked": False,
        "ignored": False,
        "worktree_root_recorded": False,
    }


@pytest.mark.parametrize("root_failure", [False, True], ids=["bad-index", "unknown-root"])
def test_registry_boundary_require_not_tracked_rejects_unavailable_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_failure: bool,
) -> None:
    from loopx.cli import main

    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(variable, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "--quiet")
    registry = repo / ".loopx" / "registry.json"
    registry.parent.mkdir()
    registry.write_text('{"goals": []}\n', encoding="utf-8")
    run_git(repo, "add", "--force", "--", ".loopx/registry.json")
    if root_failure:
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing.git"))
    else:
        bad_index = tmp_path / "bad-index"
        bad_index.write_bytes(b"DIRC")
        monkeypatch.setenv("GIT_INDEX_FILE", str(bad_index))

    exit_code = main([
        "--format", "json", "--registry", str(registry),
        "--runtime-root", str(tmp_path / "runtime"), "registry-boundary", "--require-not-tracked",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1, payload
    assert payload["ok"] is False
    assert payload["git"]["inside_worktree"] is (not root_failure)
    assert payload["git"].get("worktree_discovery_unknown", False) is root_failure
    assert payload["git"]["probe_status"] == "git_unavailable"
    assert "tracked by Git" in payload["error"]


@pytest.mark.skipif(os.name != "posix", reason="the probe's fake git is a POSIX shell script")
def test_registry_git_probe_retires_the_process_tree_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Git that fans out must not outlive the deadline that ended the probe."""

    probe = tmp_path / "project"
    probe.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grandchild = tmp_path / "grandchild.pid"
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "sleep 30 &\n"
        f"echo $! > {grandchild}\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "fake.git"))

    observed = registry_module._registry_git_probe(probe / "registry.json")
    assert observed["probe_status"] == "git_unavailable"
    assert observed["worktree_discovery_unknown"] is True

    pid = int(grandchild.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError("the probe timed out but left its grandchild running")


@pytest.mark.skipif(os.name != "posix", reason="the probe's fake git is a POSIX script")
def test_registry_git_probe_bounds_the_drain_when_a_descendant_keeps_the_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descendant in another session can hold stdout open past the deadline."""

    probe = tmp_path / "project"
    probe.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grandchild = tmp_path / "grandchild.pid"
    fake_git = bin_dir / "git"
    fake_git.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(12)'],\n"
        "    start_new_session=True)\n"
        f"open({str(grandchild)!r}, 'w', encoding='utf-8').write(str(child.pid))\n"
        "time.sleep(12)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "fake.git"))

    started = time.monotonic()
    try:
        observed = registry_module._registry_git_probe(probe / "registry.json")
        elapsed = time.monotonic() - started
    finally:
        if grandchild.exists():
            try:
                os.kill(int(grandchild.read_text(encoding="utf-8").strip() or 0), signal.SIGKILL)
            except (OSError, ValueError):
                pass

    assert observed["probe_status"] == "git_unavailable"
    assert elapsed < 6, f"the probe waited {elapsed:.2f}s on a pipe it no longer owns"
