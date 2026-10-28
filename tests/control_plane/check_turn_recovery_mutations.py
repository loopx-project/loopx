"""Check that executable assertions reject removed recovery protections.

Creates disposable sibling modules to retain real relative imports. It never
edits the production originals. Run serially; sibling modules are removed.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def check_mutation(name, module_name, test_name, old, new):
    module = ROOT / module_name
    test = ROOT / test_name
    source = module.read_text()
    assert source.count(old) == 1, f"{name}: mutation anchor drifted"
    candidate = module.with_name("_recovery_mutant_" + module.name)
    probe = test.with_name("_recovery_mutant_" + test.name)
    assert not candidate.exists() and not probe.exists()
    original = source.encode()
    try:
        candidate.write_text(source.replace(old, new))
        old_import = "../../" + module_name
        new_import = "../../" + str(candidate.relative_to(ROOT))
        assert old_import in test.read_text()
        probe.write_text(test.read_text().replace(old_import, new_import))
        run = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                "--test",
                "--test-isolation=none",
                str(probe),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # A syntax/import failure is invalid mutation evidence.
        assert run.returncode == 1 and "AssertionError" in run.stdout + run.stderr, (
            run.stdout + run.stderr
        )
        return {
            "mutation": name,
            "rejected": True,
            "exit_code": run.returncode,
            "output": run.stdout + run.stderr,
        }
    finally:
        candidate.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)
        assert module.read_bytes() == original


def lost_requirement():
    from tests.control_plane.turn_interruption_oracle import (
        INPUT_CSV,
        REQUIREMENT,
        assert_export,
    )
    import sqlite3

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "docs").mkdir()
        (root / "docs/export.csv").write_text(INPUT_CSV)
        (root / "requirements-read.txt").write_text(REQUIREMENT)
        (root / "received.txt").write_text("Repair export.")  # Lost original clause.
        db = root / "service.sqlite"
        with sqlite3.connect(db) as c:
            c.execute(
                "CREATE TABLE operations(sequence INTEGER PRIMARY KEY, body TEXT)"
            )
            c.execute("INSERT INTO operations(body) VALUES(?)", (INPUT_CSV,))
        try:
            assert_export(root, db, 1)
        except AssertionError:
            return {"mutation": "drop_original_requirement", "rejected": True}
        raise AssertionError("requirement loss was not detected")


def main():
    cases = [
        (
            "unknown_dispatch_guard",
            "loopx/control_plane/turn_driver/turn_journal.ts",
            "tests/control_plane_ts/turn_journal.test.ts",
            "&& journal.host_attempt_count !== 0",
            "&& false",
        ),
        (
            "stale_generation_guard",
            "loopx/control_plane/goals/first_party_host_runtime.ts",
            "tests/control_plane_ts/first_party_host_runtime.test.ts",
            "if (!sameGoalRef(planned.value, current.goalRef)) {",
            "if (false) {",
        ),
        (
            "current_authority_guard",
            "loopx/control_plane/goals/first_party_host_runtime.ts",
            "tests/control_plane_ts/first_party_host_runtime.test.ts",
            'if (current.activationState === "stopped") {',
            "if (false) {",
        ),
        (
            "settlement_dedup",
            "loopx/control_plane/effect_program.ts",
            "tests/control_plane_ts/effect_program.test.ts",
            '(step) => step !== "validation" && !completed.has(step)',
            '(step) => step !== "validation"',
        ),
    ]
    report = [check_mutation(*case) for case in cases] + [lost_requirement()]
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            [{k: v for k, v in row.items() if k != "output"} for row in report],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
