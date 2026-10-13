import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from pathlib import Path
import loopx
import pytest


@pytest.mark.parametrize("kind", ["output", "missing"])
def test_workspace_probe_preserves_caller_rpc(tmp_path, kind):
    release = Path(loopx.__file__).resolve().parent.parent
    fake = tmp_path / "git_child.py"
    fake.write_text(
        "import sys\nsys.stdin.buffer.read()\n"
        + (
            'print("workspace-output",flush=True)\n'
            if kind == "output"
            else "raise SystemExit(1)\n"
        )
    )
    git = tmp_path / ("fixture-git.cmd" if os.name == "nt" else "fixture-git")
    if os.name == "nt":
        git.write_text('@echo off\n"' + sys.executable + '" "' + str(fake) + '" %*\n')
    else:
        git.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " "
            + shlex.quote(str(fake))
            + ' "$@"\n'
        )
        git.chmod(0o700)
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys,json,subprocess\nsys.path.insert(0,"
        + repr(str(release))
        + ")\nfrom pathlib import Path\nfrom loopx.control_plane.agents.workspace_guard import _git_command_output,_git_origin_is_absent\nreal_run=subprocess.run\ndef run(argv,**kw):return real_run([sys.argv[2],*argv[1:]],**kw)\nsubprocess.run=run\nvalue="
        + (
            '_git_command_output(Path(sys.argv[1]),"rev-parse","--show-toplevel")'
            if kind == "output"
            else "_git_origin_is_absent(Path(sys.argv[1]))"
        )
        + '\nprint(json.dumps({"value":value}),flush=True)\nprint(json.dumps({"caller_packet":sys.stdin.readline().strip()}),flush=True)\n'
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-u", str(driver), str(tmp_path), str(git)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    queue_ = queue.Queue()
    reader = threading.Thread(
        target=lambda: queue_.put(process.stdout.readline()), daemon=True
    )
    reader.start()
    process.stdin.write("owned-rpc-packet\n")
    process.stdin.flush()
    try:
        assert json.loads(queue_.get(timeout=3)) == {
            "value": "workspace-output" if kind == "output" else True
        }
        assert json.loads(process.stdout.readline()) == {
            "caller_packet": "owned-rpc-packet"
        }
    finally:
        process.stdin.close()
        process.wait(timeout=20)
        reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
    assert process.returncode == 0
