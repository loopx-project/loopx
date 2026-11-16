import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from pathlib import Path
import loopx


def test_repository_probe_preserves_open_caller_stdin(tmp_path):
    release = Path(loopx.__file__).resolve().parent.parent
    remote = "https://example.test/team/fixture.git"
    git_script = tmp_path / "git_child.py"
    git_script.write_text(
        'import sys\nassert sys.argv[1:]==["-C",sys.argv[2],"config","--get","remote.origin.url"]\nsys.stdin.buffer.read()\nprint('
        + repr(remote)
        + ",flush=True)\n"
    )
    git_cmd = tmp_path / ("fixture-git.cmd" if os.name == "nt" else "fixture-git")
    if os.name == "nt":
        git_cmd.write_text(
            '@echo off\n"' + sys.executable + '" "' + str(git_script) + '" %*\n'
        )
    else:
        git_cmd.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " "
            + shlex.quote(str(git_script))
            + ' "$@"\n'
        )
        git_cmd.chmod(0o700)
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys,json\nsys.path.insert(0,"
        + repr(str(release))
        + ')\nfrom pathlib import Path\nfrom loopx.repository_identity import _origin_remote\ntry:\n value=_origin_remote(Path(sys.argv[1]),sys.argv[2])\n print(json.dumps({"remote":value}),flush=True)\nexcept Exception as exc:print(json.dumps({"error":repr(exc)}),flush=True)\nprint(json.dumps({"caller_packet":sys.stdin.readline().strip()}),flush=True)\n'
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-u", str(driver), str(tmp_path), str(git_cmd)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    messages = queue.Queue()
    reader = threading.Thread(
        target=lambda: messages.put(process.stdout.readline()), daemon=True
    )
    reader.start()
    process.stdin.write("owned-rpc-packet\n")
    process.stdin.flush()
    try:
        response = json.loads(messages.get(timeout=3))
        assert response == {"remote": remote}, response
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
