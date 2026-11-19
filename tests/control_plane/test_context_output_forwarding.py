"""Execute the shipped code-mode example against its result-state contract."""
import json
import shutil
import subprocess

import pytest

from loopx.control_plane.turn_driver.decision_capture import CODE_MODE_EXEC_RESULT_FORWARDER


def test_code_mode_example_keeps_pending_handles_and_failed_output():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node runtime is required")
    results = [
        {"exit_code": 0, "output": '{"ok":true,"content":"complete🙂"}'},
        {"session_id": 41, "output": "", "chunk_id": "pending"},
        {"exit_code": 19, "output": "capture digest mismatch", "chunk_id": "failure"},
    ]
    script = ("const results=JSON.parse(process.argv[1]);const visible=[];"
              "const text=value=>visible.push(value);for(const result of results){"
              + CODE_MODE_EXEC_RESULT_FORWARDER + ";}process.stdout.write(JSON.stringify(visible));")
    completed = subprocess.run([node, "-e", script, json.dumps(results)],
                               capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(completed.stdout) == [results[0]["output"], results[1], results[2]]
