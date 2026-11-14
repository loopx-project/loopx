"""The desktop release backup must fail when its remote inventory is unavailable."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/desktop-release-artifacts.yml"
)


def backup_script():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = next(i for i, line in enumerate(lines) if line.strip() == "- name: Back up existing desktop release assets")
    run = next(i for i in range(step + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run + 1]) - len(lines[run + 1].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


class DesktopReleaseBackupTests(unittest.TestCase):
    def test_inventory_failure_cannot_report_an_empty_backup(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is required to run the workflow step")
        for fail_at in ("release", "assets"):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                gh = bin_dir / "gh"
                gh.write_text(
                    '#!/bin/sh\nprintf called > "$GH_CALLED"\n'
                    'if [ "$GH_FAIL_AT" = release ] || [ "$2" = --paginate ]; then exit 23; fi\n'
                    'printf "42\\n"\n',
                    encoding="utf-8",
                )
                gh.chmod(0o755)
                env = {
                    **os.environ,
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "GH_CALLED": str(root / "gh-called"),
                    "GH_FAIL_AT": fail_at,
                    "GITHUB_REPOSITORY": "loopx-project/loopx",
                    "RELEASE_TAG": "v1.2.4",
                }
                result = subprocess.run(
                    [bash, "-c", backup_script()], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertTrue((root / "gh-called").exists())
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "dist/previous-desktop/ASSET-INVENTORY.txt").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "release upload runs on Ubuntu")
    def test_successful_inventory_preserves_assets_and_their_hashes(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is required to run the workflow step")
        for name in ("", "LoopX_1.2.4_aarch64.dmg"):
            with self.subTest(asset=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                gh = bin_dir / "gh"
                gh.write_text(
                    '#!/bin/sh\n'
                    'if [ "$1" = api ]; then\n'
                    '  if [ "$2" = --paginate ]; then [ -z "$GH_ASSET" ] || printf "%s\\n" "$GH_ASSET"; else printf "42\\n"; fi\n'
                    'else cp "$GH_SOURCE/$GH_ASSET" "dist/previous-desktop/$GH_ASSET"; fi\n',
                    encoding="utf-8",
                )
                gh.chmod(0o755)
                source = root / "remote"
                source.mkdir()
                if name:
                    (source / name).write_bytes(b"old desktop asset")
                env = {
                    **os.environ,
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "GH_ASSET": name,
                    "GH_SOURCE": str(source),
                    "GITHUB_REPOSITORY": "loopx-project/loopx",
                    "RELEASE_TAG": "v1.2.4",
                }
                result = subprocess.run(
                    [bash, "-c", backup_script()], cwd=root, env=env,
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                backup = root / "dist/previous-desktop"
                self.assertIn(f"asset_count={int(bool(name))}", (backup / "ASSET-INVENTORY.txt").read_text())
                if name:
                    self.assertEqual((backup / name).read_bytes(), b"old desktop asset")
                    digest = hashlib.sha256(b"old desktop asset").hexdigest()
                    self.assertIn(f"{digest}  {name}", (backup / "PREVIOUS-DESKTOP-SHA256SUMS").read_text())


if __name__ == "__main__":
    unittest.main()
