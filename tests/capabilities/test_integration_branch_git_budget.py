from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from loopx.capabilities.integration_branch import core


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
    )
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo


def test_a_finished_read_keeps_text_and_exit_status(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    result = core._git(repo, "rev-parse", "--verify", "HEAD")
    assert result.returncode == 0
    assert isinstance(result.stdout, str)
    assert len(result.stdout.strip()) == 40

    missing = core._git(repo, "rev-parse", "--verify", "absent", check=False)
    assert missing.returncode != 0

    with pytest.raises(core.IntegrationBranchError):
        core._git(repo, "rev-parse", "--verify", "absent")


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_a_git_that_never_answers_stops_inside_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(
        "#!/bin/sh\n"
        f"while :; do echo tick >> {shlex.quote(str(heartbeats))}; sleep 0.05; done\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(core, "GIT_READ_TIMEOUT_SECONDS", 1.0)

    started = time.monotonic()
    with pytest.raises(core.IntegrationBranchError, match="did not answer within"):
        core._git(repo, "rev-parse", "--show-toplevel")
    elapsed = time.monotonic() - started
    assert elapsed < 6, f"an unanswered read held the caller for {elapsed:.1f}s"

    time.sleep(0.2)
    retired = heartbeats.stat().st_size
    assert retired > 0, "the fixture never ran, so this proves nothing"
    time.sleep(0.5)
    assert heartbeats.stat().st_size == retired, (
        "the helper and its child outlived the read budget"
    )


def test_a_read_that_cannot_start_still_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)

    def cannot_start(command, **kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(core.subprocess, "Popen", cannot_start)

    with pytest.raises(OSError, match="git is not installed"):
        core._git(repo, "rev-parse", "--show-toplevel")


def _worktree_list(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.skipif(os.name != "posix", reason="the hook is a POSIX script")
def test_a_timed_out_worktree_add_leaves_no_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A budget that expires inside post-checkout must still unregister it.

    Git registers the worktree in the common dir before the hook runs, so the
    command never returns while the registration already exists.
    """

    repo = _repository(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nsleep 3\n", encoding="utf-8")
    hook.chmod(0o755)

    scratch = tmp_path / "scratch-root"
    monkeypatch.setattr(core.tempfile, "mkdtemp", lambda prefix=None: str(scratch))
    monkeypatch.setattr(core, "GIT_READ_TIMEOUT_SECONDS", 0.4)

    before = _worktree_list(repo)
    with pytest.raises(core.IntegrationBranchError, match="did not answer within"):
        core._build_candidate(repo, start_sha=head, sources=[])
    after = _worktree_list(repo)

    assert after == before, "the candidate stayed registered after the timeout"
    assert "prunable" not in after
    assert not scratch.exists(), "the temporary root survived the timeout"

    hook.unlink()
    recovered, failure = core._build_candidate(repo, start_sha=head, sources=[])
    assert failure is None
    assert recovered == head
    assert _worktree_list(repo) == before
