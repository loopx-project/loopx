"""Doctor's Git reads must survive non-UTF-8 host locales."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx import doctor_git

pytestmark = pytest.mark.filterwarnings(
    "error::pytest.PytestUnhandledThreadExceptionWarning"
)


@pytest.fixture
def git_repo(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            encoding="utf-8",
        ).stdout.strip()

    git("init")
    git("config", "user.email", "loopx@example.invalid")
    git("config", "user.name", "LoopX Test")
    git("commit", "--allow-empty", "-m", "fixture")
    return tmp_path, git


@pytest.fixture
def gbk_host(monkeypatch):
    # Same decoder selection used by Windows cp936, with real subprocess pipes.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")


@pytest.mark.parametrize("detached", [False, True])
def test_git_metadata_preserves_unicode_refs(git_repo, gbk_host, detached):
    root, git = git_repo
    ref = "修复中文🚀"
    if detached:
        git("tag", ref)
        git("checkout", "--detach")
    else:
        git("branch", "-m", ref)

    metadata = doctor_git.git_metadata_for_root(root)
    assert metadata["git_commit"] == git("rev-parse", "HEAD")
    assert metadata["git_ref"] == ref
    assert metadata["git_dirty"] is False
    (root / "中文.txt").write_text("fixture", encoding="utf-8")
    assert doctor_git.git_metadata_for_root(root)["git_dirty"] is True


def test_trusted_release_preserves_unicode_remote_and_ref(git_repo, gbk_host):
    root, git = git_repo
    commit = git("rev-parse", "HEAD")
    git("remote", "add", "上游🚀", "https://github.com/example/project.git")
    git("update-ref", "refs/remotes/上游🚀/发布", commit)

    trusted = doctor_git.trusted_release_ref_for_root(
        root, repository="example/project", ref="发布"
    )
    assert trusted is not None
    assert trusted["git_commit"] == commit
    assert trusted["git_ref"] == "上游🚀/发布"
    assert (
        doctor_git.trusted_release_ref_for_root(
            root, repository="someone-else/project", ref="发布"
        )
        is None
    )


@pytest.fixture
def git_output(monkeypatch, gbk_host, tmp_path):
    def install(*, returncode=0, fail_at=None):
        calls = []
        (tmp_path / ".git").mkdir(exist_ok=True)

        def run(command, **kwargs):
            args = command[3:]
            if args == ["rev-parse", "--show-toplevel"]:
                return SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n")
            calls.append(args)
            code = (
                returncode[len(calls) - 1]
                if isinstance(returncode, tuple)
                else returncode
            )
            if fail_at is not None and args[: len(fail_at)] != fail_at:
                code = 0
            if args == ["remote"]:
                stdout = b"origin\n"
            elif args[:2] == ["remote", "get-url"]:
                stdout = b"https://github.com/example/project.git\n"
            elif args[0] == "symbolic-ref":
                stdout = b"before\xffafter\n"
            elif args[0] == "rev-parse":
                stdout = b"a" * 40 + b"\n"
            else:
                stdout = b""
            script = (
                "import sys; "
                f"sys.stdout.buffer.write({stdout!r}); "
                "sys.stderr.buffer.write(b'warning: \\xff'); "
                f"sys.exit({code})"
            )
            return subprocess.run(
                [sys.executable, "-c", script], check=kwargs.pop("check"), **kwargs
            )

        monkeypatch.setattr(doctor_git, "subprocess", SimpleNamespace(run=run))
        return calls

    return install


def test_metadata_replaces_malformed_bytes(git_output, tmp_path):
    git_output()
    metadata = doctor_git.git_metadata_for_root(tmp_path)
    assert metadata["git_commit"] == "a" * 40
    assert metadata["git_ref"] == "before\ufffdafter"
    assert metadata["git_dirty"] is False


@pytest.mark.parametrize(
    ("returncodes", "expected"),
    [
        ((0, 1), "installed_ahead"),
        ((1, 0), "installed_behind"),
        ((1, 1), "diverged"),
        ((128, 1), "unknown"),
    ],
)
def test_revision_relation_tolerates_malformed_stderr(
    git_output, tmp_path, returncodes, expected
):
    calls = git_output(returncode=returncodes)
    relation = doctor_git.git_revision_relation(
        tmp_path, installed_commit="a" * 40, comparison_commit="b" * 40
    )
    assert relation == expected
    assert len(calls) == 2


@pytest.mark.parametrize(
    "fail_at", [None, ["remote"], ["remote", "get-url"], ["rev-parse"]]
)
def test_trusted_release_tolerates_malformed_stderr(git_output, tmp_path, fail_at):
    calls = git_output(returncode=128 if fail_at else 0, fail_at=fail_at)
    trusted = doctor_git.trusted_release_ref_for_root(
        tmp_path, repository="example/project", ref="main"
    )
    if fail_at:
        assert trusted is None
    else:
        assert trusted is not None
        assert trusted["git_commit"] == "a" * 40
        assert trusted["git_ref"] == "origin/main"
        assert len(calls) == 3


def test_metadata_failure_remains_unavailable(git_output, tmp_path):
    git_output(returncode=128)
    metadata = doctor_git.git_metadata_for_root(tmp_path)
    assert metadata["git_commit"] is None
    assert metadata["git_ref"] is None
    assert metadata["git_dirty"] is None
