"""Cancelling the public-boundary scan must retire the Git read it started.

The scan launches Git in a session of its own so an exhausted budget can retire
the helpers Git starts. That also keeps a terminal Ctrl-C away from Git: the
interrupt reaches the process group of the running `check`, so only the waiting
read can retire the session before the cancellation keeps travelling.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_RUN_SCAN = (
    "import sys\n"
    "from pathlib import Path\n"
    "from loopx.contract import scan_public_boundary\n"
    "scan_public_boundary([Path(sys.argv[1])])\n"
)


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "f@e.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    (repo / "ACTIVE_GOAL_STATE.md").write_text("# tracked private state\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "ACTIVE_GOAL_STATE.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


@pytest.mark.skipif(os.name != "posix", reason="a terminal Ctrl-C is a POSIX group signal")
def test_a_cancelled_scan_takes_the_git_it_started(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    stop = tmp_path / "stop"
    real_git = shutil.which("git")
    assert real_git is not None
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
        [sys.executable, "-c", _RUN_SCAN, str(repo)],
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
        assert command.poll() is None, "the scan had already returned"
        os.killpg(os.getpgid(command.pid), signal.SIGINT)
        command.communicate(timeout=25)
        assert command.returncode != 0
        retired = heartbeats.stat().st_size
        time.sleep(0.6)
        assert heartbeats.stat().st_size == retired, (
            "the blocked probe and its helper outlived the cancellation"
        )
    finally:
        stop.write_text("stop\n", encoding="utf-8")
        if command.poll() is None:
            os.killpg(os.getpgid(command.pid), signal.SIGKILL)
            command.communicate(timeout=25)
