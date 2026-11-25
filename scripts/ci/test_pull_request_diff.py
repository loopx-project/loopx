"""Exercise the shipped CI commands against real Git comparisons and paged PR reads."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from review_gate import JOB_OUTPUT


GATE = Path(__file__).resolve().with_name("review_gate.py")
PR_ENDPOINT = "repos/owner/fixture/pulls/17"
FILES_ENDPOINT = PR_ENDPOINT + "/files?per_page=100"
TARGET_ENDPOINT = "repos/owner/fixture/branches/release%2Fstable"

# The replacement sits on PATH only for the local test. Git and the CI
# entrypoint are real. The API refuses any method other than GET.
GH_FIXTURE = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
assert sys.argv[1:4] == ["api", "--method", "GET"], sys.argv
endpoint = sys.argv[-1]
assert ("--paginate" in sys.argv and "--slurp" in sys.argv) == endpoint.endswith("per_page=100")
root = Path(os.environ["PR_DIFF_TEST_INPUT"])
calls = root.with_suffix(".calls")
previous = calls.read_text().splitlines() if calls.exists() else []
with calls.open("a") as log:
    log.write(endpoint + "\\n")
responses = json.loads(root.read_text())[endpoint]
result = responses[previous.count(endpoint)]
if result is None:
    sys.exit(1)
print(json.dumps(result))
"""


@unittest.skipUnless(os.name == "posix", "CI workflow adapter runs on a POSIX GitHub runner")
class PullRequestDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-qb", "release/stable")
        self.git("config", "user.name", "CI Fixture")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.write(".github/workflows/python-tests.yml",
                   "jobs:\n  test-shard:\n    strategy:\n      matrix:\n        shard: [1, 2]\n")
        self.write("docs/old.md")
        self.base = self.commit("base")
        self.write("docs/upstream.md")
        self.target = self.commit("unrelated target advance")
        self.git("switch", "-qc", "work", self.base)
        self.git("mv", "docs/old.md", "docs/new.md")
        self.earlier_head = self.commit("rename a documentation file")
        self.files = [f"docs/file-{index}.md" for index in range(100)]
        for name in self.files:
            self.write(name)
        self.head = self.commit("many documented paths")
        self.rows = [{"filename": path, "status": "added"} for path in self.files] + [
            {"filename": "docs/new.md", "status": "renamed", "previous_filename": "docs/old.md"}
        ]
        self.pr = {
            "number": 17, "state": "open", "changed_files": len(self.rows),
            "base": {"sha": self.base, "ref": "release/stable",
                     "repo": {"id": 1, "full_name": "owner/fixture"}},
            "head": {"sha": self.head, "ref": "work", "repo": {"id": 2}},
        }
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH_FIXTURE)
        gh.chmod(0o700)
        self.responses = self.root / "responses.json"
        self.event = self.root / "event.json"
        self.plan = self.root / "impact-plan.json"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_REPOSITORY": "owner/fixture",
            "GITHUB_EVENT_PATH": str(self.event),
            "PR_DIFF_TEST_INPUT": str(self.responses),
        }
        self.env.pop("GH_TOKEN", None)
        self.env.pop("GITHUB_TOKEN", None)

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def write(self, path: str, content: str = "fixture\n") -> None:
        file = self.repo / path
        file.parent.mkdir(exist_ok=True, parents=True)
        file.write_text(content)

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def command(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GATE), *args], cwd=self.repo, env=env or self.env,
            text=True, capture_output=True, timeout=10,
        )

    def qualify(self, *, pages=..., snapshots=None, target=None, inspect=False, base=None):
        self.event.write_text(json.dumps({"pull_request": self.pr}))
        self.responses.write_text(json.dumps({
            PR_ENDPOINT: snapshots if snapshots is not None else [self.pr, self.pr],
            FILES_ENDPOINT: [[self.rows[:100], self.rows[100:]] if pages is ... else pages],
            TARGET_ENDPOINT: [{"commit": {"sha": target or self.target}}],
        }))
        self.plan.unlink(missing_ok=True)
        self.responses.with_suffix(".calls").unlink(missing_ok=True)
        if inspect:
            return self.command("inspect-pr-diff")
        return self.command("classify", "--verify-pr-diff", "--base", base or self.base,
                            "--head", self.head, "--plan", str(self.plan))

    def test_complete_paginated_rename_and_harmless_target_advance(self) -> None:
        refreshed = deepcopy(self.pr)
        refreshed["base"]["sha"] = self.target
        result = self.qualify(snapshots=[self.pr, refreshed])
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(self.plan.read_text())
        self.assertTrue(packet["published_diff"]["verified"])
        self.assertEqual(packet["merge_base_sha"], self.base)
        self.assertEqual(packet["change_kind"], "docs")
        self.assertFalse(packet["python_tests"])
        self.assertEqual(self.responses.with_suffix(".calls").read_text().splitlines(),
                         [PR_ENDPOINT, FILES_ENDPOINT, PR_ENDPOINT, TARGET_ENDPOINT])
        inspected = self.qualify(snapshots=[self.pr, refreshed], inspect=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIn("Verified the published PR inventory", inspected.stdout)

    def test_comparison_mismatch_fails_before_any_exemption(self) -> None:
        for files in ([self.rows[0]], self.rows + [{"filename": "loopx/extra.py", "status": "added"}]):
            with self.subTest(count=len(files)):
                live = {**self.pr, "changed_files": len(files)}
                result = self.qualify(pages=[files[:100], files[100:]], snapshots=[live, live])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("differs from the exact Git comparison", result.stderr)
                self.assertFalse(self.plan.exists())
                self.assertEqual(result.stdout, "")
                needs = {"changes": {"result": "failure"},
                         **{name: {"result": "skipped"} for name in JOB_OUTPUT}}
                gate = self.command("verify", env={**self.env, "NEEDS_JSON": json.dumps(needs)})
                self.assertNotEqual(gate.returncode, 0)
                self.assertIn("change classification did not succeed", gate.stderr)

    def test_unavailable_or_partial_pages_force_full_qualification(self) -> None:
        capped = {**self.pr, "changed_files": 3001}
        cases = (
            ([self.rows[:100]], [self.pr, self.pr]),
            ([self.rows[:100], [self.rows[0]]], [self.pr, self.pr]),
            (None, [self.pr, self.pr]),
            (None, [capped]),
        )
        for pages, snapshots in cases:
            with self.subTest(count=snapshots[0]["changed_files"], page_count=len(pages or [])):
                result = self.qualify(pages=pages, snapshots=snapshots)
                self.assertEqual(result.returncode, 0, result.stderr)
                packet = json.loads(self.plan.read_text())
                self.assertFalse(packet["published_diff"]["verified"])
                self.assertEqual(packet["change_kind"], "full")
                self.assertTrue(packet["backend_tests"] and packet["python_tests"] and packet["stage2c_tests"])

    def test_head_source_target_and_state_changes_invalidate_observation(self) -> None:
        for field in ("head", "source", "target", "state", "count"):
            with self.subTest(field=field):
                after = deepcopy(self.pr)
                if field == "head":
                    after["head"]["sha"] = self.earlier_head
                elif field == "source":
                    after["head"]["repo"]["id"] = 3
                elif field == "target":
                    after["base"]["ref"] = "other"
                elif field == "state":
                    after["state"] = "closed"
                else:
                    after["changed_files"] -= 1
                result = self.qualify(snapshots=[self.pr, after])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("qualify the current PR", result.stderr)
                self.assertFalse(self.plan.exists())

    def test_wrong_event_or_changed_effective_base_requires_new_qualification(self) -> None:
        wrong = self.qualify(base=self.earlier_head)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("must match the pull request event", wrong.stderr)
        self.assertFalse(self.plan.exists())
        changed = self.qualify(target=self.earlier_head)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("merge base changed", changed.stderr)
        self.assertFalse(self.plan.exists())


if __name__ == "__main__":
    unittest.main()
