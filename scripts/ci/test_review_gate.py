"""An intentionally exempt job must be skipped; all required jobs must pass."""
from __future__ import annotations
import copy
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


if __name__ == "__main__":
    unittest.main()
