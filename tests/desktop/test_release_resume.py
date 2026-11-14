"""Run the release workflow against an isolated fake GitHub release."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_release_backup import WORKFLOW, workflow_script
from test_update_feed import FAKE_GH


class DesktopReleaseResumeTests(unittest.TestCase):
    def fixture(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is required to run the workflow step")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        dist = root / "dist/desktop"
        dist.mkdir(parents=True)
        names = (
            "LoopX.app.zip",
            "LoopX_1.2.4_aarch64.dmg",
            "LoopX_1.2.4_x64-setup.exe",
            "LoopX_1.2.4_x64_en-US.msi",
        )
        for name in names:
            (dist / name).write_bytes(("fixture:" + name).encode())
        (dist / "DESKTOP-SHA256SUMS").write_text(
            "".join(f"{hashlib.sha256((dist / name).read_bytes()).hexdigest()}  {name}\n" for name in sorted(names)),
            encoding="ascii",
        )
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(WORKFLOW.parents[2] / "scripts/desktop_release_artifacts.py", scripts)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text('#!/bin/sh\nexec python "$GH_FAKE_SCRIPT" "$@"\n', encoding="utf-8")
        gh.chmod(0o755)
        fake_script = root / "fake-gh.py"
        fake_script.write_text(FAKE_GH, encoding="utf-8")
        remote = root / "remote"
        (remote / "v1.2.4").mkdir(parents=True)
        env = {
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "GH_FAKE_SCRIPT": str(fake_script),
            "GH_LOG": str(root / "gh.log"),
            "GH_REMOTE": str(remote),
            "GITHUB_REPOSITORY": "loopx-project/loopx",
            "RELEASE_TAG": "v1.2.4",
            "RELEASE_VERSION": "1.2.4",
            "SOURCE_REF": "a" * 40,
            "CHANNEL": "stable",
        }
        return bash, root, env, remote / "v1.2.4"

    def execute(self, fixture, step="Resume desktop release without replacing assets"):
        bash, root, env, _ = fixture
        return subprocess.run(
            [bash, "-c", workflow_script(step)],
            cwd=root, env=env, capture_output=True, text=True, timeout=30, check=False,
        )

    def test_interrupted_release_resumes_and_repeated_execution_is_idempotent(self):
        for interrupted in ("LoopX.app.zip", "DESKTOP-SHA256SUMS"):
            with self.subTest(interrupted=interrupted):
                fixture = self.fixture()
                _, root, env, remote = fixture
                env["GH_FAIL"] = "upload:" + interrupted
                self.assertNotEqual(self.execute(fixture).returncode, 0)
                self.assertFalse((remote / "DESKTOP-SHA256SUMS").exists())
                self.assertGreater(len(list(remote.iterdir())), 0)
                del env["GH_FAIL"]
                resumed = self.execute(fixture)
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                uploads = [line for line in (root / "gh.log").read_text().splitlines() if line.startswith("release upload ")]
                self.assertTrue(uploads[-1].startswith("release upload v1.2.4 dist/desktop/DESKTOP-SHA256SUMS"))
                self.assertFalse(any("--clobber" in line for line in uploads))
                again = self.execute(fixture)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(uploads, [line for line in (root / "gh.log").read_text().splitlines() if line.startswith("release upload ")])
                for artifact in (root / "dist/desktop").iterdir():
                    self.assertEqual((remote / artifact.name).read_bytes(), artifact.read_bytes())
                verified = self.execute(fixture, "Verify published desktop release")
                self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_existing_conflict_or_wrong_tag_commit_stops_all_uploads(self):
        for failure in ("content", "case", "manifest", "commit", "inventory"):
            with self.subTest(failure=failure):
                fixture = self.fixture()
                _, root, env, remote = fixture
                if failure == "content":
                    (remote / "LoopX_1.2.4_x64-setup.exe").write_bytes(b"different")
                elif failure == "case":
                    (remote / "loopx.app.zip").write_bytes(b"different")
                elif failure == "manifest":
                    (remote / "DESKTOP-SHA256SUMS").write_bytes(b"different")
                elif failure == "commit":
                    env["GH_REMOTE_REF"] = "b" * 40
                else:
                    env["GH_FAIL"] = "api:unavailable"
                original = {p.name: p.read_bytes() for p in remote.iterdir()}
                self.assertNotEqual(self.execute(fixture).returncode, 0)
                self.assertEqual({p.name: p.read_bytes() for p in remote.iterdir()}, original)
                self.assertFalse(any(line.startswith("release upload ") for line in (root / "gh.log").read_text().splitlines()))

    def test_corrupt_remote_readback_cannot_publish_the_manifest(self):
        fixture = self.fixture()
        _, _, env, remote = fixture
        env["GH_CORRUPT"] = "LoopX_1.2.4_aarch64.dmg"
        self.assertNotEqual(self.execute(fixture).returncode, 0)
        self.assertFalse((remote / "DESKTOP-SHA256SUMS").exists())

    def test_consistent_but_different_published_set_fails_final_verification(self):
        fixture = self.fixture()
        _, root, _, remote = fixture
        names = [p.name for p in (root / "dist/desktop").iterdir() if p.name != "DESKTOP-SHA256SUMS"]
        for name in names:
            (remote / name).write_bytes(("other:" + name).encode())
        (remote / "DESKTOP-SHA256SUMS").write_text(
            "".join(f"{hashlib.sha256((remote / name).read_bytes()).hexdigest()}  {name}\n" for name in sorted(names)),
            encoding="ascii",
        )
        self.assertNotEqual(self.execute(fixture, "Verify published desktop release").returncode, 0)

    def test_release_and_manual_republication_use_separate_steps(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for name, event in (
            ("Resume desktop release without replacing assets", "release"),
            ("Replace complete desktop binary set", "workflow_dispatch"),
            ("Replace desktop checksum manifest last", "workflow_dispatch"),
        ):
            with self.subTest(step=name):
                step = workflow.split("- name: " + name, 1)[1].split("- name:", 1)[0]
                self.assertIn(f"if: github.event_name == '{event}'", step)


if __name__ == "__main__":
    unittest.main()
