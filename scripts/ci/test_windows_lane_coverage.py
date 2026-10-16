"""The native Windows lane must run the suites written for native Windows.

#6356 hardened nine control-plane TypeScript suites so their assertions hold on
native Windows, #6258 flushed the state-backup archive through writable handles
and #6562 fixed the checkpoint durability flush; every one of those fixes
changed behavior that only a native Windows host exercises. The curated
``windows-powershell`` list still ran none of the suites, so the checkpoint
regression shipped with green qualification. Pin the suites to that lane: a
later edit that drops one, or a new one that arrives with the same Windows-only
behavior, has to update this contract instead of silently losing the coverage.

The check is deliberately lexical but pins the whole qualification, so a suite
cannot stay "listed" without being executed on Windows and gating the lane:

* the step must be one single-line ``node --test`` command over exactly the
  listed files, so a shell comment, extra plumbing, a trailing ``exit 0`` or a
  multi-line block cannot skip or mask part of the list;
* a bare trailing path on its own shell line can be a silent no-op in
  PowerShell while ``split()`` still sees the same tokens, which is why the
  single-line shape is part of the contract;
* neither the step, the lane nor the workflow may inject ``NODE_OPTIONS``,
  which could turn the command into a no-op (``--test-only``,
  ``--test-name-pattern``) while the lane stays green;
* the step must not be skipped (``if``), muted (``continue-on-error``) or
  redefined through a YAML merge key, and the lane must not be made
  non-blocking either;
* the job must still run on a pinned Windows runner, depend on the change
  classification, and activate on ``python_tests`` exactly.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/python-tests.yml"
ACTIVATION = "needs.changes.outputs.python_tests == 'true'"
NODE_OPTION_DENIAL = "NODE_OPTIONS could turn the suite command into a no-op"
QUALIFY_COMMAND = (
    "node",
    "--no-warnings",
    "--experimental-sqlite",
    "--experimental-strip-types",
    "--test",
)

# Suite -> the native Windows fix that made it host-qualified.
WINDOWS_QUALIFIED_SUITES = (
    "tests/control_plane_ts/agent_preferences.test.ts",  # #6356
    "tests/control_plane_ts/authority_archive.test.ts",  # #6356
    "tests/control_plane_ts/canonical_task_lease_renew.test.ts",  # #6356
    "tests/control_plane_ts/checkpoint_artifacts.test.ts",  # #6562
    "tests/control_plane_ts/cold_source_import.test.ts",  # #6356
    "tests/control_plane_ts/coordination_runtime_shadow.test.ts",  # #6356
    "tests/control_plane_ts/effect_runtime_snapshot.test.ts",  # #6356
    "tests/control_plane_ts/goal_acceptance_authority.test.ts",  # #6356
    "tests/control_plane_ts/quota_void_commit.test.ts",  # #6356
    "tests/control_plane_ts/shadow_entry_delivery.test.ts",  # #6356
    "tests/control_plane_ts/sqlite_backup.test.ts",  # #6258
)


def workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def windows_lane() -> dict:
    return workflow()["jobs"]["windows-powershell"]


class WindowsLaneCoverageTests(unittest.TestCase):
    def test_windows_lane_still_qualifies_every_code_change(self):
        # The lane is the only native Windows qualification, so it must keep
        # running on Windows, keep following the change classification the other
        # required jobs use, keep activating on exactly that classification, and
        # keep gating the merge.
        parsed = workflow()
        lane = parsed["jobs"]["windows-powershell"]
        self.assertIn("windows", lane["runs-on"])
        self.assertNotIn("${{", lane["runs-on"], "the lane must pin a Windows runner, not an expression")
        self.assertIn("changes", lane["needs"])
        self.assertNotIn("continue-on-error", lane, "the lane must gate the merge, not observe a failure")
        self.assertEqual(lane["if"].strip(), ACTIVATION)
        self.assertNotIn("NODE_OPTIONS", parsed.get("env") or {}, NODE_OPTION_DENIAL)
        self.assertNotIn("NODE_OPTIONS", lane.get("env") or {}, NODE_OPTION_DENIAL)

    def test_windows_qualified_suites_are_unconditionally_executed_in_the_lane(self):
        steps = [step for step in windows_lane()["steps"]
                 if any(suite in step.get("run", "") for suite in WINDOWS_QUALIFIED_SUITES)]
        for suite in WINDOWS_QUALIFIED_SUITES:
            with self.subTest(suite=suite):
                self.assertTrue((ROOT / suite).is_file(), f"{suite} is not a suite in this checkout")
        self.assertTrue(steps, "no native Windows lane step mentions a host-qualified suite")
        for step in steps:
            with self.subTest(step=step["name"]):
                self.assertNotIn("\n", step["run"], "the suites must run as one single-line command")
                self.assertEqual(
                    step["run"].split(),
                    [*QUALIFY_COMMAND, *WINDOWS_QUALIFIED_SUITES],
                    "the host-qualified suites must run as one plain node command",
                )
                self.assertNotIn("if", step, "the host-qualified suites must not be skipped")
                self.assertNotIn("continue-on-error", step, "the host-qualified suites must block the lane")
                self.assertNotIn("<<", step, "the host-qualified suites step must not use YAML merge keys")
                self.assertNotIn("NODE_OPTIONS", step.get("env") or {}, NODE_OPTION_DENIAL)


if __name__ == "__main__":
    unittest.main()
