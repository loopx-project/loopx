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
            "core_tests": True, "backend_tests": False, "frontend_tests": True,
            "compat_tests": False, "python_tests": False,
            "stage2c_tests": False, "presentation_tests": True,
        })
        self.assertEqual(job_flags("backend"), {
            "core_tests": True, "backend_tests": True, "frontend_tests": False,
            "compat_tests": False, "python_tests": False,
            "stage2c_tests": False, "presentation_tests": False,
        })
        self.assertEqual(job_flags("mixed"), {
            "core_tests": True, "backend_tests": True, "frontend_tests": True,
            "compat_tests": False, "python_tests": False,
            "stage2c_tests": False, "presentation_tests": True,
        })
        self.assertEqual(job_flags("full"), {
            "core_tests": True, "backend_tests": True, "frontend_tests": True,
            "compat_tests": True, "python_tests": True,
            "stage2c_tests": True, "presentation_tests": False,
        })
        self.assertFalse(any(job_flags("docs").values()))

    def test_presentation_and_docs_union(self):
        for status in ("A", "M", "D"):
            for path in ("apps/presentation/dashboard/src/App.tsx", "loopx/web/chat/assets/chat.js",
                         "apps/presentation/dashboard/public/icon.svg"):
                self.assertEqual(candidate([Change(status, path), Change("M", "docs/design.md")])[0], "presentation")

    def test_root_readme_locales_are_documents(self):
        readmes = ("README.md", "README.zh-CN.md", "README.ja.md")
        for path in readmes:
            for status in ("A", "M", "D"):
                with self.subTest(path=path, status=status):
                    self.assertEqual(candidate([Change(status, path)])[0], "docs")
        self.assertEqual(candidate([Change("M", path) for path in readmes])[0], "docs")

    def test_backend_mixed_and_full_risk_tiering(self):
        for path in ("loopx/cli.py", "loopx/control_plane/goals/vision_checkpoint.ts",
                     "loopx/claude_goal_mode/commands/loopx.md", "tests/test_ui.py",
                     "AGENTS.md", "docs/build.py",
                     "packages/dsh-loopx-plugin/src/cli.ts", "packages/loopx-ark-turn/tests/test_cli.py",
                     "packages/dsh-loopx-plugin/smoke/dsh-legacy-host.package.json",
                     "tests/fixtures/change_quality/oracles/typescript/package.json"):
            with self.subTest(backend_path=path):
                self.assertEqual(candidate([Change("M", path)])[0], "backend")
                self.assertEqual(candidate([Change("M", "loopx/web/chat/index.html"), Change("M", path)])[0], "mixed")
        self.assertEqual(candidate([Change("D", "loopx/code.py"), Change("A", "docs/code.md")])[0], "backend")
        for path in ("apps/desktop/src/main.rs", "loopx/web/chat/backend.py", "loopx/web/chat/config.json"):
            with self.subTest(mixed_or_non_presentation=path):
                self.assertNotEqual(candidate([Change("M", "loopx/web/chat/index.html"), Change("M", path)])[0], "presentation")
        for path in ("apps/presentation/dashboard/package-lock.json", "apps/presentation/dashboard/vite.config.ts",
                     "apps/desktop/loopx-control-plane/src-tauri/Cargo.toml",
                     "examples/dsh-loopx-demo/package.json",
                     "packages/loopx-capability-benchmark-toolkit/pyproject.toml",
                     "packages/loopx-jev/uv.lock",
                     "packages/loopx-memt-turn/MANIFEST.in",
                     "packages/dsh-loopx-plugin/package.json",
                     "packages/dsh-loopx-plugin/pnpm-lock.yaml",
                     "packages/dsh-loopx-plugin/pnpm-workspace.yaml",
                     "scripts/ci/impact_plan.py", ".github/workflows/python-tests.yml",
                     "pyproject.toml", "package-lock.json", "examples/shared-goal-authority-e2e/mutants.py",
                     "packages/loopx-core/prepare.py", "unknown"):
            with self.subTest(full_path=path):
                self.assertEqual(candidate([Change("M", path)])[0], "full")
                self.assertEqual(candidate([Change("M", "loopx/web/chat/index.html"), Change("M", path)])[0], "full")

    def test_invalid_empty_and_non_pr_are_full(self):
        for changes in ([], [Change("T", "docs/a.md")], [Change("M", "docs/../code.md")],
                        [Change("M", "/docs/a.md")], [Change("M", "docs//a.md")]):
            self.assertEqual(candidate(changes)[0], "full")
        self.assertEqual(candidate([Change("M", "docs/a.md")], pull_request=False)[0], "full")
        self.assertEqual(candidate([Change("M", "loopx/cli.py")], pull_request=False)[0], "full")

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
            def classify(base, *extra, head="HEAD"):
                result = subprocess.run([sys.executable, script, "classify", "--base", base, "--head", head,
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
                write(policy, "jobs:\n  test-shard:\n    strategy:\n      matrix:\n        shard: [1, 2, 3, 4, 5, 6]\n"
                      if policy.endswith(".yml") else "reviewed policy")
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
            self.assertEqual(packet["python_shards"], 0)
            self.assertTrue(packet["presentation_tests"])
            self.assertEqual(packet["head_sha"], git("rev-parse", "HEAD"))
            for flag in ("--force-full", "--non-pr"):
                forced, _ = classify(base, flag)
                self.assertTrue(forced["python_tests"])
                self.assertTrue(forced["backend_tests"])
                self.assertTrue(forced["stage2c_tests"])
                self.assertEqual(forced["python_shards"], 6)
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
            backend_only, _ = classify(before_backend)
            self.assertEqual(backend_only["change_kind"], "backend")
            self.assertTrue(backend_only["core_tests"])
            self.assertTrue(backend_only["backend_tests"])
            self.assertFalse(backend_only["frontend_tests"])
            self.assertFalse(backend_only["compat_tests"])
            self.assertFalse(backend_only["python_tests"])
            self.assertFalse(backend_only["stage2c_tests"])
            self.assertFalse(backend_only["presentation_tests"])
            self.assertEqual(backend_only["python_shards"], 0)
            forced_backend, _ = classify(before_backend, "--force-full")
            self.assertEqual(forced_backend["change_kind"], "full")
            self.assertEqual(forced_backend["python_shards"], 6)
            self.assertTrue(forced_backend["compat_tests"])
            write(path, "later client change")
            git("commit", "-qam", "latest client commit")
            cumulative, _ = classify(before_backend)
            self.assertEqual(cumulative["change_kind"], "mixed")
            self.assertTrue(cumulative["backend_tests"])
            self.assertTrue(cumulative["frontend_tests"])
            self.assertTrue(cumulative["presentation_tests"])
            self.assertFalse(cumulative["python_tests"])
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
            # The workflow executing this checkout owns topology, even when
            # the candidate head differs or an uncommitted file is misleading.
            published = git("rev-parse", "HEAD")
            workflow = POLICY_PATHS[-1]
            prefix = "jobs:\n  test-shard:\n    strategy:\n      matrix:\n"
            write(workflow, prefix + "        shard: [1, 2, 3, 4, 5, 6, 7, 8]\n")
            self.assertEqual(classify(published, "--non-pr")[0]["python_shards"], 6)
            git("commit", "-qam", "executing workflow has eight shards")
            packet, _ = classify(published, "--non-pr", head=published)
            self.assertEqual(packet["python_shards"], 8)
            self.assertEqual(packet["head_sha"], published)
            self.assertEqual(packet["checkout_sha"], git("rev-parse", "HEAD"))
            for axis in ("shard: []", "shard: [1, 1]", "shard: [1, 3]", "shard: [1, '02']",
                         "shard: '${{ inputs.shards }}'", "shard: [1, 2]\n        python: ['3.11']", None):
                with self.subTest(axis=axis):
                    write(workflow, prefix + "        " + axis + "\n" if axis else "jobs: {}\n")
                    git("commit", "-qam", "unsupported execution topology")
                    output = root / "rejected.json"
                    result = subprocess.run([sys.executable, script, "classify", "--base", "HEAD",
                        "--head", "HEAD", "--non-pr", "--plan", str(output)],
                        cwd=root, env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("test-shard.strategy.matrix", result.stderr)
                    self.assertFalse(output.exists(), "invalid topology must not publish a fallback plan")
            # The docs exemption still reports no Python coverage or shards.
            docs_base = git("rev-parse", "HEAD")
            write("README.md", "fixture documentation")
            git("add", "--", "README.md")
            git("commit", "-qm", "docs-only change")
            packet, _ = classify(docs_base)
            self.assertEqual(packet["change_kind"], "docs")
            self.assertFalse(packet["python_tests"])
            self.assertEqual(packet["python_shards"], 0)

    def test_subpackage_dependency_manifests_require_full_under_trusted_policy(self):
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

            git("init", "-q")
            git("config", "user.name", "CI Fixture")
            git("config", "user.email", "ci@example.invalid")
            git("config", "core.hooksPath", str(root / "no-hooks"))
            for policy in POLICY_PATHS:
                write(
                    policy,
                    "jobs:\n  test-shard:\n    strategy:\n      matrix:\n        shard: [1, 2, 3, 4, 5, 6]\n"
                    if policy.endswith(".yml")
                    else "reviewed policy",
                )
            for manifest_path in (
                "packages/loopx-capability-benchmark-toolkit/pyproject.toml",
                "packages/dsh-loopx-plugin/package.json",
                "packages/dsh-loopx-plugin/pnpm-lock.yaml",
            ):
                write(manifest_path, "initial")
            git("add", ".")
            git("commit", "-qm", "trusted base")

            for manifest_path in (
                "packages/loopx-capability-benchmark-toolkit/pyproject.toml",
                "packages/dsh-loopx-plugin/package.json",
                "packages/dsh-loopx-plugin/pnpm-lock.yaml",
            ):
                with self.subTest(manifest_path=manifest_path):
                    base = git("rev-parse", "HEAD")
                    write(manifest_path, f"updated {manifest_path}")
                    git("add", "--", manifest_path)
                    git("commit", "-qm", f"update {manifest_path}")
                    plan_file = root / "plan.json"
                    classified = subprocess.run(
                        [sys.executable, script, "classify", "--base", base, "--head", "HEAD", "--plan", str(plan_file)],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(classified.returncode, 0, classified.stderr)
                    packet = json.loads(plan_file.read_text())
                    self.assertEqual(packet["change_kind"], "full")
                    self.assertTrue(packet["core_tests"])
                    self.assertTrue(packet["backend_tests"])
                    self.assertTrue(packet["frontend_tests"])
                    self.assertTrue(packet["compat_tests"])
                    self.assertTrue(packet["python_tests"])
                    self.assertTrue(packet["stage2c_tests"])
                    self.assertFalse(packet["presentation_tests"])
                    self.assertEqual(packet["python_shards"], 6)
                    outputs = {
                        "change_kind": packet["change_kind"],
                        **{
                            key: str(bool(packet[key])).lower()
                            for key in (
                                "core_tests", "backend_tests", "frontend_tests",
                                "compat_tests", "python_tests", "stage2c_tests", "presentation_tests",
                            )
                        },
                    }
                    all_full = {
                        "changes": {"result": "success", "outputs": outputs},
                        "checks": {"result": "success"},
                        "pytest": {"result": "success"},
                        "node-minimum-compatibility": {"result": "success"},
                        "stage2c-correctness-e2e": {"result": "success"},
                        "windows-powershell": {"result": "success"},
                        "presentation": {"result": "skipped"},
                    }
                    ok = subprocess.run(
                        [sys.executable, script, "verify"],
                        cwd=root,
                        env={**env, "NEEDS_JSON": json.dumps(all_full)},
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(ok.returncode, 0, ok.stderr)
                    for blocked in ("pytest", "node-minimum-compatibility", "stage2c-correctness-e2e", "windows-powershell"):
                        skipped_needs = {**all_full, blocked: {"result": "skipped"}}
                        bad = subprocess.run(
                            [sys.executable, script, "verify"],
                            cwd=root,
                            env={**env, "NEEDS_JSON": json.dumps(skipped_needs)},
                            capture_output=True,
                            text=True,
                        )
                        self.assertNotEqual(bad.returncode, 0, blocked)


if __name__ == "__main__":
    unittest.main()
