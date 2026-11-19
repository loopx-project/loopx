"""Fail closed before advertising incomplete updates."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "desktop_feed", Path(__file__).resolve().parents[2] / "scripts/desktop_update_feed.py"
)
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)
WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "desktop-updater.yml"
)
# What Tauri signs with: base64 of a minisign signature whose lines are the
# untrusted comment, a 74-byte signature, a trusted comment and a 64-byte
# global signature. Matches the shape of published channel signatures.
TEST_SIGNATURE = base64.b64encode(
    b"untrusted comment: signature from tauri secret key\n"
    + base64.b64encode(b"\x00" * 74)
    + b"\ntrusted comment: timestamp:0\n"
    + base64.b64encode(b"\x00" * 64)
).decode("ascii")


class FeedTests(unittest.TestCase):
    def test_only_core_stable_releases_advance_pointer(self):
        pointer = None
        for tag, prerelease in [
            ("v1.0.0", False),
            ("dsh-loopx-plugin-v0.1.1-beta.4", False),
            ("v1.1.0-rc.1", True),
            ("v1.1.0", True),
            ("v-plugin-2.0.0", False),
        ]:
            if feed.release_channel(tag, prerelease) == "stable":
                pointer = tag
        self.assertEqual(pointer, "v1.0.0")

    def test_stable_pointer_never_moves_backward_and_can_repair_the_same_version(self):
        current = {"version": "1.2.4"}
        self.assertEqual(
            feed.channel_pointer_decision("stable", current, {"version": "1.2.5"}),
            "advance",
        )
        self.assertEqual(
            feed.channel_pointer_decision("stable", current, {"version": "1.2.4"}),
            "advance",
        )
        self.assertEqual(
            feed.channel_pointer_decision("stable", current, {"version": "1.2.3"}),
            "retain",
        )

    def test_main_pointer_uses_run_before_attempt_for_monotonic_order(self):
        current = {"version": "0.0.0-main.37235947793.1"}
        self.assertEqual(
            feed.channel_pointer_decision(
                "main", current, {"version": "0.0.0-main.37235947794.1"}
            ),
            "advance",
        )
        self.assertEqual(
            feed.channel_pointer_decision(
                "main", current, {"version": "0.0.0-main.37235947793.2"}
            ),
            "advance",
        )
        self.assertEqual(
            feed.channel_pointer_decision(
                "main", current, {"version": "0.0.0-main.37235947792.9"}
            ),
            "retain",
        )

    def test_pointer_decision_rejects_malformed_or_cross_channel_feeds(self):
        for channel, current, candidate in [
            ("stable", {"version": "unknown"}, {"version": "1.2.5"}),
            ("stable", {"version": "1.2.4"}, {"version": "0.0.0-main.9.1"}),
            ("main", {"version": "1.2.4"}, {"version": "0.0.0-main.9.1"}),
            ("main", {"version": "0.0.0-main.9.1"}, {"version": "1.2.5"}),
        ]:
            with self.subTest(channel=channel, current=current, candidate=candidate):
                with self.assertRaises(ValueError):
                    feed.channel_pointer_decision(channel, current, candidate)

    def test_workflow_compares_the_existing_feed_before_replacing_the_pointer(self):
        publish = WORKFLOW.read_text(encoding="utf-8").split(
            "- name: Upload immutable artifacts, publish channel pointer last", 1
        )[1]
        ordered_steps = [
            'gh release download "$pointer" --pattern desktop-updater.json',
            '--channel-pointer-decision "$CHANNEL"',
            'if [ "$decision" = advance ]; then',
            'gh release upload "$pointer" dist/updater/desktop-updater.json --clobber',
        ]
        positions = [publish.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("already points to an equal or newer build", publish)

    def test_workflow_builds_and_uploads_the_signed_windows_installer(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("BUNDLE: nsis", workflow)
        self.assertIn("dist/updater/*.exe", workflow)
        feed_step = workflow.split("- name: Generate complete feed", 1)[1].split(
            "- name: Upload immutable artifacts", 1
        )[0]
        self.assertIn('--channel "$CHANNEL"', feed_step)
        self.assertIn("CHANNEL: ${{ needs.identity.outputs.channel }}", feed_step)

    def test_workflow_gates_the_signed_windows_build_and_checks_its_version(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        macos_step = workflow.split(
            "- name: Build signed macOS App and exact bundled runtime", 1
        )[1].split("- name: Build signed Windows NSIS updater installer", 1)[0]
        windows_step = workflow.split(
            "- name: Build signed Windows NSIS updater installer", 1
        )[1].split("- name: Collect native updater artifacts", 1)[0]
        self.assertIn("if: runner.os == 'macOS'", macos_step)
        self.assertIn('\\"createUpdaterArtifacts\\":true', macos_step)
        self.assertIn('\\"signingIdentity\\":\\"-\\"', macos_step)
        self.assertIn("if: runner.os == 'Windows'", windows_step)
        self.assertIn('\\"createUpdaterArtifacts\\":true', windows_step)
        self.assertNotIn("signingIdentity", windows_step)
        collect_step = workflow.split(
            "- name: Collect native updater artifacts", 1
        )[1].split("- uses: actions/upload-artifact", 1)[0]
        self.assertIn("-updater-setup.exe", collect_step)
        self.assertIn("*-updater-setup.exe*) continue", collect_step)
        self.assertIn('ls dist/updater/*"$VERSION"*-updater-setup.exe', collect_step)
        self.assertIn("VERSION: ${{ needs.identity.outputs.version }}", collect_step)

    def test_stable_publish_does_not_require_the_windows_build(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        build_macos = workflow.split("\n  build-macos:\n", 1)[1].split(
            "\n  build-windows:\n", 1
        )[0]
        build_windows = workflow.split("\n  build-windows:\n", 1)[1].split(
            "\n  publish:\n", 1
        )[0]
        self.assertIn(
            "if: needs.identity.outputs.channel == 'stable' || needs.identity.outputs.channel == 'main'",
            build_macos,
        )
        self.assertIn("if: needs.identity.outputs.channel == 'main'", build_windows)
        self.assertIn("name: signed-updater-windows-latest", build_windows)

        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn(
            "needs: [identity, build-macos, build-windows, validate]", publish
        )
        condition = publish.split("if: >-", 1)[1].split("runs-on:", 1)[0]
        expression = " ".join(line.strip() for line in condition.splitlines())
        python_expression = (
            expression.replace("always()", "True")
            .replace("&&", " and ")
            .replace("||", " or ")
            .replace("needs.identity.result", "identity_result")
            .replace("needs.build-macos.result", "darwin_result")
            .replace("needs.build-windows.result", "windows_result")
            .replace("needs.validate.result", "validate_result")
            .replace("needs.identity.outputs.channel", "channel")
        )

        def publish_after(
            channel,
            identity_result="success",
            darwin_result="success",
            windows_result="skipped",
            validate_result="success",
        ):
            return eval(  # noqa: S307 - repository-owned expression, no builtins.
                python_expression,
                {"__builtins__": {}},
                {
                    "channel": channel,
                    "identity_result": identity_result,
                    "darwin_result": darwin_result,
                    "windows_result": windows_result,
                    "validate_result": validate_result,
                },
            )

        self.assertTrue(
            publish_after("stable", windows_result="failure"),
            "a Windows failure must not withhold the Darwin-only stable feed",
        )
        self.assertFalse(
            publish_after("main", windows_result="failure"),
            "a main release must reject a failed Windows build",
        )
        self.assertFalse(
            publish_after("main", windows_result="skipped"),
            "a main release must reject a missing Windows build",
        )
        self.assertTrue(publish_after("main", windows_result="success"))

    def _upload_step_script(self):
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        step = next(
            index
            for index, line in enumerate(lines)
            if line.strip()
            == "- name: Upload immutable artifacts, publish channel pointer last"
        )
        run = next(
            index
            for index in range(step + 1, len(lines))
            if lines[index].strip() == "run: |"
        )
        script_indent = len(lines[run + 1]) - len(lines[run + 1].lstrip())
        body = []
        for line in lines[run + 1 :]:
            if line.strip() and len(line) - len(line.lstrip()) < script_indent:
                break
            body.append(line[script_indent:])
        return "\n".join(body).rstrip()

    def _working_bash(self):
        candidates = [os.environ.get("BASH"), shutil.which("bash")]
        git = shutil.which("git")
        if git:
            candidates.append(str(Path(git).resolve().parents[1] / "bin" / "bash.exe"))
        if os.name == "nt":
            candidates.extend(
                [
                    r"C:\Program Files\Git\bin\bash.exe",
                    r"C:\Program Files\Git\usr\bin\bash.exe",
                ]
            )
        for candidate in candidates:
            if not candidate:
                continue
            try:
                probe = subprocess.run(
                    [candidate, "-c", "exit 0"],
                    capture_output=True,
                    timeout=10,
                )
            except OSError:
                continue
            if probe.returncode == 0:
                return candidate
        self.skipTest("a working bash is required for the upload-step regression")

    def _run_upload_step(self, channel, version, tag, include_windows):
        bash = self._working_bash()
        script = self._upload_step_script()
        case_dir = self.root / f"upload-{channel}-{int(include_windows)}"
        updater = case_dir / "dist" / "updater"
        updater.mkdir(parents=True)
        for name in ("LoopX.app.tar.gz", "LoopX.app.tar.gz.sig"):
            shutil.copy2(self.root / name, updater / name)
        if include_windows:
            for name in (
                "LoopX_0.0.0-main.123.1_x64-updater-setup.exe",
                "LoopX_0.0.0-main.123.1_x64-updater-setup.exe.sig",
            ):
                shutil.copy2(self.root / name, updater / name)
        payload = feed.build(updater, version, tag, channel)
        (updater / "desktop-updater.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        bin_dir = case_dir / "bin"
        bin_dir.mkdir()
        gh_log = case_dir / "gh.log"
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$GH_LOG"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_LOG": str(gh_log),
            "RELEASE_TAG": tag,
            "SOURCE_REF": "source-ref",
            "EVENT_NAME": "release",
            "CHANNEL": channel,
        }
        result = subprocess.run(
            [bash, "-c", script],
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return gh_log.read_text(encoding="utf-8").splitlines()

    def test_upload_step_skips_absent_windows_artifact_on_stable(self):
        stable = self._run_upload_step("stable", "1.2.4", "v1.2.4", False)
        self.assertIn("release upload v1.2.4 dist/updater/LoopX.app.tar.gz", stable)
        self.assertIn(
            "release upload v1.2.4 dist/updater/LoopX.app.tar.gz.sig", stable
        )
        self.assertIn(
            "release upload v1.2.4 dist/updater/desktop-updater.json --clobber",
            stable,
        )
        self.assertIn(
            "release upload desktop-stable dist/updater/desktop-updater.json --clobber",
            stable,
        )
        self.assertFalse(any(".exe" in line for line in stable))
        self.assertFalse(any("*" in line for line in stable))

        main = self._run_upload_step(
            "main", "0.0.0-main.123.1", "desktop-main-123-1", True
        )
        self.assertIn(
            "release upload desktop-main-123-1 "
            "dist/updater/LoopX_0.0.0-main.123.1_x64-updater-setup.exe",
            main,
        )
        self.assertIn(
            "release upload desktop-main dist/updater/desktop-updater.json --clobber",
            main,
        )
        feed_upload = next(
            index
            for index, line in enumerate(main)
            if "dist/updater/desktop-updater.json --clobber" in line
            and "desktop-main " not in line
        )
        pointer_upload = next(
            index
            for index, line in enumerate(main)
            if line.startswith("release upload desktop-main ")
        )
        self.assertLess(feed_upload, pointer_upload)

    def test_upload_step_rejects_main_without_windows_artifact(self):
        case_dir = self.root / "upload-main-missing"
        updater = case_dir / "dist" / "updater"
        updater.mkdir(parents=True)
        for name in ("LoopX.app.tar.gz", "LoopX.app.tar.gz.sig"):
            shutil.copy2(self.root / name, updater / name)
        with self.assertRaises(ValueError):
            feed.build(updater, "0.0.0-main.123.1", "desktop-main-123-1", "main")

    def test_workflow_pointer_upload_stays_inside_the_advance_branch(self):
        publish = WORKFLOW.read_text(encoding="utf-8").split(
            "- name: Upload immutable artifacts, publish channel pointer last", 1
        )[1]
        lines = publish.splitlines()
        branch = next(
            index
            for index, line in enumerate(lines)
            if 'if [ "$decision" = advance ]; then' in line
        )
        upload = next(
            index
            for index, line in enumerate(lines)
            if 'gh release upload "$pointer"' in line
        )
        closing = next(
            index
            for index, line in enumerate(lines[upload:], start=upload)
            if line.strip() == "fi"
        )

        def indentation(line):
            return len(line) - len(line.lstrip())

        self.assertGreater(indentation(lines[upload]), indentation(lines[branch]))
        self.assertEqual(indentation(lines[closing]), indentation(lines[branch]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / "LoopX.app.tar.gz"
        self.artifact.write_bytes(b"test archive")
        self.signature = self.root / "LoopX.app.tar.gz.sig"
        self.signature.write_text(TEST_SIGNATURE)
        self.windows = self.root / "LoopX_0.0.0-main.123.1_x64-updater-setup.exe"
        self.windows.write_bytes(b"test installer")
        self.windows_signature = (
            self.root / "LoopX_0.0.0-main.123.1_x64-updater-setup.exe.sig"
        )
        self.windows_signature.write_text(TEST_SIGNATURE)

    def build(self):
        return feed.build(self.root, "0.0.0-main.123.1", "desktop-main-123-1", "main")

    def test_immutable_source_and_qualified_platform(self):
        result = self.build()
        self.assertEqual(
            set(result["platforms"]), {"darwin-aarch64", "windows-x86_64-nsis"}
        )
        self.assertEqual(result["platforms"]["darwin-aarch64"]["url"],
                         "https://github.com/loopx-project/loopx/releases/download/desktop-main-123-1/LoopX.app.tar.gz")
        self.assertEqual(
            result["platforms"]["windows-x86_64-nsis"]["url"],
            "https://github.com/loopx-project/loopx/releases/download/desktop-main-123-1/LoopX_0.0.0-main.123.1_x64-updater-setup.exe",
        )

    def test_stable_channel_excludes_windows_until_that_rollout_is_approved(self):
        # The signed Windows installer is attached to stable releases for
        # manual installs, but the stable feed must not advertise it yet.
        result = feed.build(self.root, "1.2.4", "v1.2.4", "stable")
        self.assertEqual(set(result["platforms"]), {"darwin-aarch64"})

    def test_windows_artifact_fails_closed_on_the_main_channel(self):
        self.windows.unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.windows.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.build()
        self.windows.write_bytes(b"installer")
        self.windows_signature.unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.windows_signature.write_text(" \n")
        with self.assertRaises(ValueError):
            self.build()

    def test_unknown_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            feed.build(self.root, "0.0.0-main.123.1", "desktop-main-123-1", "nightly")

    def test_missing_empty_and_duplicate_artifact(self):
        self.artifact.unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.artifact.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.build()
        self.artifact.write_bytes(b"archive")
        (self.root / "Other.app.tar.gz").write_bytes(b"archive")
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_or_empty_signature(self):
        self.signature.unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.signature.write_text(" \n")
        with self.assertRaises(ValueError):
            self.build()

    def test_malformed_signature_fails_closed(self):
        # The client needs two decodable payloads of 74 and 64 bytes; a
        # partially written file that only carries the comment line, a wrong
        # payload length, or bytes a lenient decoder would silently drop must
        # not be published.
        short_signature = base64.b64encode(b"\x00" * 73)
        global_signature = base64.b64encode(b"\x00" * 64)
        for case, content in [
            ("not base64", "signature from tauri secret key"),
            ("wrong decoded contents", base64.b64encode(b"hello world").decode("ascii")),
            ("broken padding", TEST_SIGNATURE[:-1]),
            (
                "comment only",
                base64.b64encode(
                    b"untrusted comment: signature from tauri secret key\n"
                ).decode("ascii"),
            ),
            (
                "wrong payload length",
                base64.b64encode(
                    b"untrusted comment: c\n"
                    + short_signature
                    + b"\ntrusted comment: t\n"
                    + global_signature
                ).decode("ascii"),
            ),
            ("embedded newline", TEST_SIGNATURE[:40] + "\n" + TEST_SIGNATURE[40:]),
        ]:
            with self.subTest(case=case):
                self.signature.write_text(content)
                with self.assertRaises(ValueError):
                    self.build()
                # Keep the qualified Darwin signature valid so the second
                # failure is caused by the Windows signature alone.
                self.signature.write_text(TEST_SIGNATURE)
                self.windows_signature.write_text(content)
                with self.assertRaises(ValueError):
                    self.build()
                self.windows_signature.write_text(TEST_SIGNATURE)
        self.signature.write_text(TEST_SIGNATURE)

    def test_untrusted_version_or_tag(self):
        for version, tag in [("v1.0.0", "valid"), ("1.0.0", "../main"), ("1.0.0;command", "main")]:
            with self.subTest(version=version, tag=tag):
                with self.assertRaises(ValueError):
                    feed.build(self.root, version, tag, "main")
