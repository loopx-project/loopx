"""The Lark collector's config admission asks Git three questions.

Each of those reads used to be unbounded, so a Git blocked on a stale index
lock, a network mount or an oversized index held the command open with no
answer. These tests pin the shared budget, the early refusal, and the process
tree a blocked read would otherwise leave behind.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.extensions.lark import event_collector
from loopx.extensions.lark.event_collector import _project_config_path


def _git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    return project


def _config(project: Path) -> Path:
    target = project / ".loopx" / "config" / "collector.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    return target


def _fake_git(tmp_path: Path, script: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "git"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_a_git_that_cannot_start_refuses_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    _config(project)

    def cannot_start(command, **kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(event_collector.subprocess, "Popen", cannot_start)

    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_the_next_question_is_not_asked_after_one_goes_unanswered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    _config(project)
    asked = tmp_path / "asked.txt"
    asked.write_text("", encoding="utf-8")
    _fake_git(
        tmp_path,
        "#!/bin/sh\n"
        f'echo "$*" >> {shlex.quote(str(asked))}\n'
        'for argument in "$@"; do\n'
        '  case "$argument" in *--error-unmatch*) sleep 30 ;; esac\n'
        "done\n"
        'if [ "$3" = "rev-parse" ]; then printf "%s\\n" "$2"; exit 0; fi\n'
        "exit 0\n",
        monkeypatch,
    )
    monkeypatch.setattr(event_collector, "GIT_PROBE_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")
    elapsed = time.monotonic() - started

    commands = asked.read_text(encoding="utf-8")
    assert "rev-parse" in commands
    assert "--error-unmatch" in commands
    # The ignore flag of an unproven config cannot change the answer, so the
    # third read is not started at all.
    assert "check-ignore" not in commands
    assert elapsed < 10, f"the admission spent {elapsed:.1f}s on two questions"


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_a_git_that_never_answers_stays_inside_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    _config(project)
    _fake_git(tmp_path, "#!/bin/sh\nsleep 30\n", monkeypatch)
    monkeypatch.setattr(event_collector, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"the probe held the command open for {elapsed:.2f}s"


@pytest.mark.skipif(os.name != "posix", reason="core.fsmonitor needs a POSIX hook")
def test_a_timed_out_probe_takes_the_helpers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git starts an fsmonitor hook for `ls-files`, so the budget owns it too."""

    project = _git_project(tmp_path)
    _config(project)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "f@e.invalid"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Fixture"], check=True)
    (project / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-qm", "fixture"], check=True)

    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    hook = project / ".git" / "blocking-fsmonitor"
    hook.write_text(
        "#!/bin/sh\n"
        f"while :; do echo tick >> {shlex.quote(str(heartbeats))}; sleep 0.1; done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(project), "config", "core.fsmonitor", str(hook)], check=True
    )
    monkeypatch.setattr(event_collector, "GIT_PROBE_TIMEOUT_SECONDS", 0.8)

    started = time.monotonic()
    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")
    assert time.monotonic() - started < 6

    retired = heartbeats.stat().st_size
    assert retired > 0, "the fixture hook never ran, so this proves nothing"
    time.sleep(0.6)
    assert heartbeats.stat().st_size == retired, (
        "the fsmonitor hook and its child outlived the probe budget"
    )


def test_an_ignored_untracked_config_still_loads(tmp_path: Path) -> None:
    project = _git_project(tmp_path)
    target = _config(project)

    assert _project_config_path(project, ".loopx/config/collector.json") == target.resolve()


@pytest.mark.skipif(os.name != "posix", reason="a terminal Ctrl-C is a POSIX group signal")
def test_a_cancelled_command_takes_the_git_it_started(tmp_path: Path) -> None:
    """Ctrl-C during the admission retires the session the read owns.

    `collector-plan` dispatches to `plan_lark_event_collector`, so the child
    below runs that entry in its own process, as the CLI would, with a `git`
    that answers everything through real Git except the ownership read: that
    one blocks, the shape a real `core.fsmonitor` hook has while Git waits on
    it. The interrupt then goes to the child's own process group, which is the
    scope a terminal Ctrl-C reaches. Git runs in a session of its own, so the
    interrupt never reaches it: only the waiting call can retire it.
    """

    project = _git_project(tmp_path)
    _config(project)
    real_git = shutil.which("git")
    assert real_git is not None
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    stop = tmp_path / "stop"
    bin_dir = tmp_path / "blocking-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "if 'ls-files' not in sys.argv:\n"
        f"    os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n"
        f"heartbeats, stop = Path({str(heartbeats)!r}), Path({str(stop)!r})\n"
        "while not stop.exists():\n"
        "    with heartbeats.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('tick\\n')\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST", "COVERAGE", "COV_CORE"))
    }
    environment.update({
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "LOOPX_USAGE_PING": "0",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    })
    command = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys\n"
            "from loopx.extensions.lark.event_collector import plan_lark_event_collector\n"
            "plan_lark_event_collector(project=sys.argv[1], config_path=sys.argv[2])\n",
            str(project), ".loopx/config/collector.json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 25
        while heartbeats.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeats.stat().st_size > 0, "the fixture never blocked, so this proves nothing"
        assert command.poll() is None, "the command had already returned"
        os.killpg(os.getpgid(command.pid), signal.SIGINT)
        command.communicate(timeout=25)
        assert command.returncode != 0
        retired = heartbeats.stat().st_size
        time.sleep(0.6)
        assert heartbeats.stat().st_size == retired, (
            "the blocked Git and its helper outlived the cancellation"
        )
    finally:
        stop.write_text("stop\n", encoding="utf-8")
        if command.poll() is None:
            os.killpg(os.getpgid(command.pid), signal.SIGKILL)
            command.communicate(timeout=25)
