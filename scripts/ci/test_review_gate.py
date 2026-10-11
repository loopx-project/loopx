"""An intentionally exempt job must be skipped; all required jobs must pass."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from review_gate import CHECK_OUTPUT, JOB_OUTPUT, requires_core_tests, verify, verify_checks

# Independent acceptance: client-only work retains compiled frontend/browser
# qualification but does not claim backend conformance or coverage.
PROFILE_FLAGS = {
    "docs": dict(core_tests=False, backend_tests=False, python_tests=False, stage2c_tests=False, presentation_tests=False),
    "presentation": dict(core_tests=True, backend_tests=False, python_tests=False, stage2c_tests=False, presentation_tests=True),
    "full": dict(core_tests=True, backend_tests=True, python_tests=True, stage2c_tests=True, presentation_tests=False),
}
PROFILE_JOBS = {
    "docs": set(),
    "presentation": {"checks", "presentation", "dashboard-acceptance", "chat-bundle-browser"},
    "full": {"checks", "pytest", "node-minimum-compatibility", "stage2c-correctness-e2e",
             "windows-powershell", "kernel-static-checks", "typescript-coverage",
             "dashboard-acceptance", "chat-bundle-browser"},
}


def needs(kind="full", *, presentation=False, jobs=JOB_OUTPUT):
    flags = {**PROFILE_FLAGS[kind], "presentation_tests": kind == "presentation" or presentation}
    required = PROFILE_JOBS[kind] | ({"presentation"} if presentation else set())
    return {"changes": {"result": "success", "outputs": {"change_kind": kind,
        **{key: str(value).lower() for key, value in flags.items()}}},
        **{job: {"result": "success" if job in required else "skipped"} for job in jobs}}


class GateTests(unittest.TestCase):
    def test_legal_profiles_and_policy_rehearsal(self):
        for kind in ("docs", "presentation", "full"):
            verify(needs(kind))
            verify_checks(needs(kind, jobs=CHECK_OUTPUT))
        verify(needs("full", presentation=True))
        self.assertFalse(requires_core_tests(["README.md", "docs/guide.md"]))
        self.assertTrue(requires_core_tests(["loopx/prompt.md"]))
        self.assertTrue(requires_core_tests([]))

    def test_every_missing_failure_cancel_or_unexpected_skip_is_rejected(self):
        self.check_results(JOB_OUTPUT, verify)
        self.check_results(CHECK_OUTPUT, verify_checks)

    def check_results(self, jobs, verifier):
        for kind in ("docs", "presentation", "full"):
            good = needs(kind, jobs=jobs)
            for job in jobs:
                for state in ("success", "skipped", "failure", "cancelled", "neutral", None):
                    if state == good[job]["result"]:
                        continue
                    value = copy.deepcopy(good)
                    value[job]["result"] = state
                    with self.subTest(kind=kind, job=job, state=state), self.assertRaises(ValueError):
                        verifier(value)
            for job in ("changes", *jobs):
                value = copy.deepcopy(good)
                del value[job]
                with self.assertRaises(ValueError):
                    verifier(value)

    def test_bad_and_contradictory_classification_cannot_skip_work(self):
        for kind in ("docs", "presentation", "full"):
            for field in ("change_kind", "core_tests", "backend_tests", "python_tests", "stage2c_tests", "presentation_tests"):
                for replacement in (None, "", True, "unknown"):
                    value = needs(kind)
                    value["changes"]["outputs"][field] = replacement
                    with self.assertRaises((ValueError, TypeError)):
                        verify(value)
        value = needs("full")
        value["changes"]["outputs"]["stage2c_tests"] = "false"
        with self.assertRaises(ValueError):
            verify(value)
        for state in ("failure", "skipped", "cancelled"):
            value = needs()
            value["changes"]["result"] = state
            with self.assertRaises(ValueError):
                verify(value)
        with self.assertRaises(ValueError):
            verify({**needs(), "extra": {"result": "success"}})

    def test_backend_flag_cannot_waive_a_full_pr_or_create_client_coverage(self):
        for kind in ("docs", "presentation", "full"):
            for jobs, verifier in ((JOB_OUTPUT, verify), (CHECK_OUTPUT, verify_checks)):
                value = needs(kind, jobs=jobs)
                outputs = value["changes"]["outputs"]
                outputs["backend_tests"] = "false" if outputs["backend_tests"] == "true" else "true"
                with self.assertRaisesRegex(ValueError, "contradictory"):
                    verifier(value)


class PythonShardAdmissionTests(unittest.TestCase):
    """Check the shipped workflow's admission scope and executable gate."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (Path(__file__).resolve().parents[2]
                        / ".github/workflows/python-tests.yml").read_text()

    def job(self, name):
        body = self.workflow.split(f"\n  {name}:\n", 1)[1]
        return re.split(r"\n  [\w-]+:\n", body, maxsplit=1)[0]

    def script(self, job, step):
        body = self.job(job).split(f"      - name: {step}\n", 1)[1]
        body = body.split("        run: |\n", 1)[1]
        lines = []
        for line in body.splitlines():
            if line and not line.startswith("          "):
                break
            lines.append(line[10:])
        return "\n".join(lines)

    def test_required_python_gate_rejects_cancelled_overflow_and_missing_shards(self):
        script = self.script("pytest", "Require every upstream check")
        for checks in ("success", "failure", "cancelled", "skipped", ""):
            for shards in ("success", "failure", "cancelled", "skipped", ""):
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                    env={**os.environ, "CHECKS_RESULT": checks, "SHARDS_RESULT": shards},
                    capture_output=True,
                )
                with self.subTest(checks=checks, shards=shards):
                    self.assertEqual(result.returncode == 0,
                                     checks == shards == "success")
        gate = self.job("pytest")
        self.assertIn("if: always() && needs.changes.outputs.python_tests == 'true'", gate)
        self.assertIn("needs: [changes, checks, test-shard]", gate)

    def test_required_python_gate_reads_every_nonempty_coverage_partition(self):
        script = self.script("pytest", "Combine complete coverage and enforce the existing floor")
        # Execute the workflow's file guard, before the coverage tool itself.
        guard = script.split("python -m coverage combine", 1)[0]
        for absent in (None, 1, 2, 3, 4):
            for empty in (False, True):
                with tempfile.TemporaryDirectory() as directory:
                    for shard in range(1, 5):
                        path = Path(directory) / f"coverage-shards/python-coverage-{shard}/.coverage"
                        path.parent.mkdir(parents=True)
                        if shard != absent or empty:
                            path.write_bytes(b"" if shard == absent else b"partition")
                    result = subprocess.run(
                        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", guard],
                        cwd=directory, capture_output=True,
                    )
                    with self.subTest(absent=absent, empty=empty):
                        self.assertEqual(result.returncode == 0, absent is None)

    def test_long_shards_share_groups_across_current_heads_without_replacement(self):
        job = self.job("test-shard")
        admission = re.search(r"\n    concurrency:\n((?:      [^\n]*\n)+)", job)
        self.assertIsNotNone(admission, "long shards need cross-run admission")
        fields = dict(re.findall(r"^      ([\w-]+): (.+)$", admission.group(1), re.MULTILINE))
        self.assertEqual(fields.get("queue"), "max")
        self.assertEqual(fields.get("cancel-in-progress"), "false")
        template = fields["group"]
        # A ref/run/PR component would silently restore per-run admission.
        self.assertEqual(re.findall(r"\$\{\{\s*(.*?)\s*\}\}", template), ["matrix.shard"])
        groups = {re.sub(r"\$\{\{.*?\}\}", str(shard), template).casefold()
                  for shard in range(1, 5)}
        self.assertEqual(len(groups), 4)
        self.assertIn("shard: [1, 2, 3, 4]", job)
        self.assertIn("fail-fast: false", job)
        root = self.workflow.split("\njobs:\n", 1)[0]
        self.assertIn("group: python-tests-${{ github.event_name }}-${{ github.ref }}", root)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' || github.event_name == 'push' }}", root)
        self.assertIn("permissions:\n  contents: read", root)
        self.assertNotIn("secrets.", job)


if __name__ == "__main__":
    unittest.main()
