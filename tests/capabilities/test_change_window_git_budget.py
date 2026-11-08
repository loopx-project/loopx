from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from loopx.capabilities.repository_change_window import repository
from loopx.extensions import process_runtime


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


def test_a_finished_read_keeps_bytes_exit_status_and_stdin(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    result = repository.git(repo, "rev-parse", "--verify", "HEAD")
    assert result.returncode == 0
    assert len(result.stdout.strip()) == 40
    assert isinstance(result.stdout, bytes)

    missing = repository.git(
        repo, "rev-parse", "--verify", "definitely-missing", check=False
    )
    assert missing.returncode != 0

    hashed = repository.git(repo, "hash-object", "--stdin", input_bytes=b"stdin\n")
    assert hashed.returncode == 0
    assert len(hashed.stdout.strip()) == 40

    assert repository.git_text(repo, "symbolic-ref", "--short", "HEAD") == "main"


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_an_unanswered_read_refuses_instead_of_reporting_clean(
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
    monkeypatch.setattr(repository, "GIT_READ_TIMEOUT_SECONDS", 1.0)

    started = time.monotonic()
    with pytest.raises(
        repository.RepositoryChangeWindowError, match="did not answer within"
    ):
        repository.repository_has_changes(repo)
    elapsed = time.monotonic() - started
    assert elapsed < 6, f"an unanswered read held the caller for {elapsed:.1f}s"

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

    monkeypatch.setattr(repository.subprocess, "Popen", cannot_start)

    with pytest.raises(OSError, match="git is not installed"):
        repository.repository_has_changes(repo)

class _WindowsNameProxy:
    """Report Windows only to the runtime under test, keeping pathlib host-native."""

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    @property
    def name(self):
        return "nt"


_TASKKILL_FAILURES = (
    "#!/bin/sh\nwhile :; do :; done\n",
    "#!/bin/sh\nexit 1\n",
)


@pytest.mark.skipif(os.name != "posix", reason="the fake binaries are POSIX scripts")
@pytest.mark.parametrize("taskkill_script", _TASKKILL_FAILURES)
def test_a_failed_windows_cleanup_still_returns_the_read_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    taskkill_script: str,
) -> None:
    """A stuck or refused taskkill must not replace the read's own timeout."""

    repo = _repository(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\nwhile :; do :; done\n", encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    taskkill = bin_dir / "taskkill"
    taskkill.write_text(taskkill_script, encoding="utf-8")
    taskkill.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(process_runtime, "os", _WindowsNameProxy(os))
    monkeypatch.setattr(repository, "GIT_READ_TIMEOUT_SECONDS", 0.25)

    started = time.monotonic()
    with pytest.raises(
        repository.RepositoryChangeWindowError, match="did not answer within"
    ):
        repository.repository_has_changes(repo)
    assert time.monotonic() - started < 3


@pytest.mark.skipif(os.name != "posix", reason="the fake binaries are POSIX scripts")
@pytest.mark.parametrize("taskkill_script", _TASKKILL_FAILURES)
def test_a_failed_windows_cleanup_still_propagates_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    taskkill_script: str,
) -> None:
    """Ctrl-C keeps travelling even when Windows termination is unconfirmed."""

    repo = _repository(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\nwhile :; do :; done\n", encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    taskkill = bin_dir / "taskkill"
    taskkill.write_text(taskkill_script, encoding="utf-8")
    taskkill.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(process_runtime, "os", _WindowsNameProxy(os))

    def interrupted(self, timeout=None, input=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        repository.repository_has_changes(repo)
    assert time.monotonic() - started < 3
