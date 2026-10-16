"""Exercise the bilingual gate through real staged and committed Git diffs."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).with_name("rfc_bilingual.py")
ROOT = "docs/architecture/rfcs/"
EN, ZH = ROOT + "alpha.md", ROOT + "alpha.zh-CN.md"


class BilingualDiffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        for path in (EN, ZH, ROOT + "legacy.md"):
            self.write(path, "Original\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True)

    def write(self, path, text="Changed\n"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self):
        # This is a disposable synthetic repository, never the source checkout.
        self.git("add", "--all")
        self.git("commit", "-q", "-m", "fixture")

    def check(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.repo,
            capture_output=True, text=True, check=False,
        )

    def branch_check(self):
        return self.check("--base", self.base, "--head", "HEAD")

    def test_english_only_fails_with_the_exact_missing_path(self):
        self.write(EN)
        self.commit()
        result = self.branch_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn(ZH, result.stderr)

    def test_chinese_only_fails(self):
        self.write(ZH)
        self.commit()
        self.assertEqual(self.branch_check().returncode, 1)

    def test_pair_can_be_changed_in_separate_commits(self):
        self.write(EN)
        self.commit()
        self.write(ZH)
        self.git("add", "--", ZH)
        self.assertEqual(self.check("--base", self.base, "--staged").returncode, 0)
        self.commit()
        self.assertEqual(self.branch_check().returncode, 0)

    def test_only_staged_changes_satisfy_the_commit_check(self):
        self.write(EN)
        self.git("add", "--", EN)
        self.write(ZH)
        self.assertEqual(self.check("--staged").returncode, 1)
        self.git("add", "--", ZH)
        self.assertEqual(self.check("--staged").returncode, 0)

    def test_target_branch_change_does_not_satisfy_the_pr_pair(self):
        self.git("checkout", "-q", "-b", "feature")
        self.write(EN)
        self.commit()
        feature = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "-b", "target", self.base)
        self.write(ZH)
        self.commit()
        result = self.check("--base", "HEAD", "--head", feature)
        self.assertEqual(result.returncode, 1)
        self.assertIn(ZH, result.stderr)

    def test_deletion_requires_both_mirrors(self):
        (self.repo / EN).unlink()
        self.commit()
        self.assertEqual(self.branch_check().returncode, 1)
        (self.repo / ZH).unlink()
        self.commit()
        self.assertEqual(self.branch_check().returncode, 0)

    def test_rename_requires_both_mirrors(self):
        self.git("mv", EN, ROOT + "renamed.md")
        self.commit()
        self.assertEqual(self.branch_check().returncode, 1)
        self.git("mv", ZH, ROOT + "renamed.zh-CN.md")
        self.commit()
        self.assertEqual(self.branch_check().returncode, 0)

    def test_ledger_and_non_ascii_paths_are_covered(self):
        en = ROOT + "ledger/alpha/双语 entry.md"
        zh = ROOT + "ledger/alpha/双语 entry.zh-CN.md"
        self.write(en)
        self.write(zh)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write(en, "Revised\n")
        self.commit()
        result = self.branch_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn(zh, result.stderr)

    def test_legacy_single_language_and_unrelated_changes_pass(self):
        self.write(ROOT + "legacy.md")
        self.write("docs/ordinary.md")
        self.commit()
        self.assertEqual(self.branch_check().returncode, 0)

    def test_new_pair_passes_and_partial_new_mirror_fails(self):
        self.write(ROOT + "new.md")
        self.write(ROOT + "new.zh-CN.md")
        self.commit()
        self.assertEqual(self.branch_check().returncode, 0)
        self.write(ROOT + "legacy.zh-CN.md")
        self.commit()
        self.assertEqual(self.branch_check().returncode, 1)

    def test_missing_revision_fails_closed(self):
        self.assertEqual(self.check("--base", "absent", "--head", "HEAD").returncode, 1)

    def test_staged_head_argument_is_rejected(self):
        self.assertEqual(self.check("--staged", "--head", "HEAD").returncode, 2)


class WorkflowPlacementTest(unittest.TestCase):
    def test_every_pr_checks_pairing_before_document_exemptions(self):
        workflow = SCRIPT.parents[2] / ".github/workflows/python-tests.yml"
        steps = yaml.safe_load(workflow.read_text())["jobs"]["changes"]["steps"]
        gate = next(i for i, s in enumerate(steps) if s.get("name") == "Require paired RFC language changes")
        classify = next(i for i, s in enumerate(steps) if s.get("id") == "classify")
        self.assertLess(gate, classify)
        self.assertEqual(steps[gate]["if"], "github.event_name == 'pull_request'")
        self.assertEqual(steps[gate]["env"]["BASE_SHA"], "${{ github.event.pull_request.base.sha }}")
        self.assertEqual(steps[gate]["env"]["HEAD_SHA"], "${{ github.event.pull_request.head.sha }}")
        self.assertIn('--base "$BASE_SHA" --head "$HEAD_SHA"', steps[gate]["run"])
        self.assertEqual(steps[0]["with"]["fetch-depth"], 0)


if __name__ == "__main__":
    unittest.main()
