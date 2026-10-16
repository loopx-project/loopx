"""Exercise the bilingual gate through real staged and committed Git diffs."""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_rfc_bilingual.py"
ROOT = "docs/architecture/rfcs/"
EN, ZH = ROOT + "alpha.md", ROOT + "alpha.zh-CN.md"


class GitFixture(unittest.TestCase):
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


class BilingualDiffTest(GitFixture):
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


class PremergeTest(GitFixture):
    def setUp(self):
        super().setUp()
        from loopx.canary.planner import DEFAULT_CATALOG_PATH

        checker = self.repo / "scripts/check_rfc_bilingual.py"
        checker.parent.mkdir()
        shutil.copyfile(SCRIPT, checker)
        examples = self.repo / "examples"
        examples.mkdir()
        (examples / "fixture-smoke.py").write_text("print('fixture')\n")
        self.write("fixture.txt", "Synthetic premerge validation input\n")
        catalog = self.repo / "docs/concepts/interaction-pattern-catalog.md"
        catalog.parent.mkdir()
        shutil.copyfile(DEFAULT_CATALOG_PATH, catalog)
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def premerge_cli(self):
        # This small repository supplies an explicit validation selector rather
        # than claiming it contains LoopX's full semantic-canary source tree.
        # RFC pairing must still inspect its complete real committed Git diff.
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "canary", "premerge",
             "--changed-file", "fixture.txt", "--git-diff-base", self.base,
             "--tier", "quick", "--no-progress"],
            cwd=self.repo, capture_output=True, text=True, check=False,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result, json.loads(result.stdout)

    def test_real_premerge_cli_rejects_a_single_language_even_in_quick_tier(self):
        self.write(EN)
        self.commit()
        result, payload = self.premerge_cli()
        check = next(c for c in payload["direct_checks"] if c["id"] == "rfc_bilingual_diff")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["gate"]["merge_gate_passed"])
        self.assertEqual(check["status"], "failed")
        self.assertIn(ZH, check["stderr_tail"])

    def test_real_premerge_cli_accepts_a_committed_pair(self):
        self.write(EN)
        self.write(ZH)
        self.commit()
        result, payload = self.premerge_cli()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(payload["gate"]["merge_gate_passed"])
        check = next(c for c in payload["direct_checks"] if c["id"] == "rfc_bilingual_diff")
        self.assertEqual(check["status"], "passed")

    def test_omitted_change_selector_cannot_skip_the_direct_gate(self):
        from loopx.canary.premerge import build_premerge_validation_gate

        self.write(EN)
        self.commit()
        payload = build_premerge_validation_gate(
            repo_root=self.repo, changed_files=[], base_ref=self.base, tier="quick", execute=True,
        )
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["gate"]["merge_gate_passed"])
        self.assertEqual(payload["gate"]["direct_failure_count"], 1)

    def test_uncommitted_translation_does_not_satisfy_the_merge_gate(self):
        self.write(EN)
        self.commit()
        self.write(ZH)
        result, payload = self.premerge_cli()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["gate"]["merge_gate_passed"])

    def test_deleting_a_registered_checker_fails_instead_of_skipping(self):
        (self.repo / "scripts/check_rfc_bilingual.py").unlink()
        self.write(EN)
        self.commit()
        result, payload = self.premerge_cli()
        self.assertEqual(result.returncode, 1)
        check = next(c for c in payload["direct_checks"] if c["id"] == "rfc_bilingual_diff")
        self.assertEqual(check["status"], "failed")

    def test_preview_does_not_claim_a_passed_merge_gate(self):
        from loopx.canary.premerge import build_premerge_validation_gate

        payload = build_premerge_validation_gate(
            repo_root=self.repo, changed_files=[EN], base_ref=self.base, tier="quick", execute=False,
        )
        check = next(c for c in payload["direct_checks"] if c["id"] == "rfc_bilingual_diff")
        self.assertEqual(check["status"], "ready")
        self.assertFalse(payload["gate"]["merge_gate_passed"])


class UnregisteredRepositoryTest(GitFixture):
    def test_other_repositories_retain_their_original_direct_checks(self):
        from loopx.canary.premerge import build_premerge_validation_gate

        payload = build_premerge_validation_gate(
            repo_root=self.repo, changed_files=[], base_ref=self.base, tier="quick", execute=True,
        )
        self.assertEqual(
            [c["id"] for c in payload["direct_checks"]],
            ["diff_check_committed", "diff_check_staged", "diff_check_unstaged"],
        )
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
