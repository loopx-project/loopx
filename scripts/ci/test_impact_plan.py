"""Whole-PR boundaries, trusted policy and real Git negative controls."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from impact_plan import Change, POLICY_PATHS, candidate, job_flags


class ImpactTests(unittest.TestCase):
    def test_whole_pr_job_requirements_are_owned_by_the_change_kind(self):
        self.assertEqual(job_flags("presentation"), {
            "core_tests": True, "backend_tests": False, "python_tests": False,
            "stage2c_tests": False, "presentation_tests": True,
        })
        self.assertEqual(job_flags("full"), {
            "core_tests": True, "backend_tests": True, "python_tests": True,
            "stage2c_tests": True, "presentation_tests": False,
        })
        self.assertFalse(any(job_flags("docs").values()))

    def test_presentation_and_docs_union(self):
        for status in ("A", "M", "D"):
            for path in ("apps/presentation/dashboard/src/App.tsx", "loopx/web/chat/assets/chat.js",
                         "apps/presentation/dashboard/public/icon.svg"):
                self.assertEqual(candidate([Change(status, path), Change("M", "docs/design.md")])[0], "presentation")

    def test_one_shared_unknown_prompt_test_or_dependency_forces_full(self):
        for path in ("loopx/cli.py", "loopx/control_plane/goals/vision_checkpoint.ts",
                     "loopx/claude_goal_mode/commands/loopx.md", "tests/test_ui.py",
                     "apps/presentation/dashboard/package-lock.json", "apps/presentation/dashboard/vite.config.ts",
                     "apps/desktop/src/main.rs", "scripts/ci/impact_plan.py", ".github/workflows/python-tests.yml",
                     "loopx/web/chat/backend.py", "loopx/web/chat/config.json", "unknown", "docs/build.py"):
            with self.subTest(path=path):
                self.assertEqual(candidate([Change("M", "loopx/web/chat/index.html"), Change("M", path)])[0], "full")

    def test_invalid_empty_non_pr_and_move_out_of_runtime_are_not_exempt(self):
        for changes in ([], [Change("T", "docs/a.md")], [Change("M", "docs/../code.md")],
                        [Change("M", "/docs/a.md")], [Change("M", "docs//a.md")],
                        [Change("D", "loopx/code.py"), Change("A", "docs/code.md")]):
            self.assertEqual(candidate(changes)[0], "full")
        self.assertEqual(candidate([Change("M", "docs/a.md")], pull_request=False)[0], "full")

    def test_real_git_policy_force_full_and_type_boundaries(self):
        script = str(Path(__file__).with_name("review_gate.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "COVERAGE", "COV_CORE"))}
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, env=env, text=True).strip()
            def write(path, content):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            def classify(base, *extra):
                result = subprocess.run([sys.executable, script, "classify", "--base", base, "--head", "HEAD",
                    "--plan", str(root / "plan.json"), *extra], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads((root / "plan.json").read_text()), result.stdout
            git("init", "-q")
            git("config", "user.name", "CI Fixture")
            git("config", "user.email", "ci@example.invalid")
            git("config", "core.hooksPath", str(root / "no-hooks"))
            path = "loopx/web/chat/index.html"
            write(path, "old")
            for policy in POLICY_PATHS:
                write(policy, "reviewed policy")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            write(path, "new")
            git("commit", "-qam", "presentation")
            packet, _ = classify(base)
            self.assertEqual(packet["change_kind"], "presentation")
            self.assertFalse(packet["python_tests"])
            self.assertFalse(packet["backend_tests"])
            self.assertFalse(packet["stage2c_tests"])
            self.assertTrue(packet["presentation_tests"])
            self.assertEqual(packet["head_sha"], git("rev-parse", "HEAD"))
            for flag in ("--force-full", "--non-pr"):
                forced, _ = classify(base, flag)
                self.assertTrue(forced["python_tests"])
                self.assertTrue(forced["backend_tests"])
                self.assertTrue(forced["stage2c_tests"])
                self.assertEqual(forced["python_shards"], 4)
            # A policy change cannot authorize its own exemption.
            write(POLICY_PATHS[0], "changed policy")
            git("commit", "-qam", "policy")
            self.assertEqual(classify(base)[0]["change_kind"], "full")
            strange = "unknown\npython_tests=false"
            write(strange, "fixture")
            git("add", strange)
            git("commit", "-qm", "unusual path")
            packet, output = classify(base)
            self.assertNotIn("python_tests=false", output)
            self.assertIn(strange, [item["path"] for item in packet["changes"]])
            before_backend = git("rev-parse", "HEAD")
            write("loopx/cli.py", "backend change")
            git("add", "loopx/cli.py")
            git("commit", "-qm", "earlier backend commit")
            write(path, "later client change")
            git("commit", "-qam", "latest client commit")
            cumulative, _ = classify(before_backend)
            self.assertEqual(cumulative["change_kind"], "full")
            self.assertTrue(cumulative["backend_tests"])
            self.assertEqual({item["path"] for item in cumulative["changes"]}, {"loopx/cli.py", path})
            before_link = git("rev-parse", "HEAD")
            (root / "docs").mkdir()
            (root / "docs/link.md").symlink_to("../loopx/web/chat/index.html")
            git("add", "docs/link.md")
            git("commit", "-qm", "symlink")
            self.assertEqual(classify(before_link)[0]["change_kind"], "full")
            failed = subprocess.run([sys.executable, script, "classify", "--base", "missing", "--head", "HEAD"],
                                    cwd=root, env=env, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
