#!/usr/bin/env python3
"""Verify distribution-only runtime resources outside the source checkout."""

import argparse
import os
import subprocess
import tempfile

CHECK = r"""
from pathlib import Path
import sys
import loopx
from loopx.canary.maintainability_ratchet import MODULE_METRIC_BASELINE_PATH, module_metric_baseline
from loopx.control_plane.effect_runtime import effect_runtime_result

package = Path(loopx.__file__).resolve()
assert package.is_relative_to(Path(sys.prefix).resolve()), package
assert module_metric_baseline(MODULE_METRIC_BASELINE_PATH)
projection = effect_runtime_result("manager.discovery.project", {
    "arguments": {},
    "sources": [{"source_id": "local", "source_host": "fixture"}],
    "pages": [{"ok": True, "matched": 1, "rows": [{"agent_id": "fixture-agent"}]}],
})
assert projection["ok"] and projection["view"] == "agents"
assert projection["matched"] == 1
assert projection["rows"][0]["agent_id"] == "fixture-agent"
assert projection["rows"][0]["source_id"] == "local"
print("Installed Manager discovery and canary baseline passed")
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="loopx-installed-runtime-") as directory:
        env = {**os.environ, "TMPDIR": directory, "LOOPX_USAGE_PING": "0"}
        subprocess.run([args.python, "-I", "-c", CHECK], cwd=directory, env=env, check=True)
