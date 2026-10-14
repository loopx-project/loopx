#!/usr/bin/env python3
"""Exercise the real M1 TypeScript builder, then kill two semantic mutations.

Synthetic receipts qualify the pure builder only. No provider, receipt writer,
consumer, or runtime admission is invoked. Mutation copies live in ignored
local state and never rewrite production source or the independent test oracle.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "loopx/control_plane/goals/direction_baseline.ts"
TEST = ROOT / "tests/control_plane_ts/direction_baseline.test.ts"


def main() -> int:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node >=22.22.3 is required for the TypeScript builder")
    command = [node, "--no-warnings", "--experimental-strip-types", "--test"]
    subprocess.run([*command, str(TEST)], cwd=ROOT, check=True)
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="direction-mutations-", dir=local) as directory:
        temporary = Path(directory)
        source = SOURCE.read_text().replace('"./vision_checkpoint.ts"',
            repr((SOURCE.parent / "vision_checkpoint.ts").as_uri()))
        mutations = [
            ("revision", "receipt.observed_revision !== revision", "false", "F2/F6"),
            ("agent", "r.agent_id === agent", "true", "F3 a different agent_id"),
        ]
        for name, original, replacement, case in mutations:
            assert source.count(original) == 1, f"mutation target changed: {name}"
            module = temporary / f"{name}.ts"
            module.write_text(source.replace(original, replacement))
            test = temporary / f"{name}.test.ts"
            test.write_text(TEST.read_text().replace(
                '"../../loopx/control_plane/goals/direction_baseline.ts"', repr(module.as_uri())))
            result = subprocess.run([*command, f"--test-name-pattern={case}", str(test)],
                cwd=ROOT, text=True, capture_output=True, check=False)
            assert result.returncode != 0 and "ERR_ASSERTION" in result.stdout, (
                f"mutation {name} must fail the semantic assertion, not setup: {result.stdout}{result.stderr}")
            print(f"mutation {name}: independent semantic assertion rejected relaxed matching")
    print("OK goal-direction-baseline-f2-smoke: actual M1 builder; synthetic receipts only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
