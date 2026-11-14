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

# Stand in for GitHub only; the actual workflow shell and filesystem remain real.
FAKE_GH = r'''import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
remote = Path(os.environ["GH_REMOTE"])
with Path(os.environ["GH_LOG"]).open("a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")
if args[0] == "api":
    endpoint = next((arg for arg in args[1:] if arg.startswith("repos/")), "")
    if os.environ.get("GH_FAIL") == "api:unavailable":
        print("HTTP/2.0 503 Service Unavailable\n\n{}")
        sys.exit(1)
    if "commits/" in endpoint:
        if "--include" in args:
            missing = os.environ.get("GH_TAG_MISSING") == "1"
            print("HTTP/2.0 404 Not Found\n\n{}" if missing else "HTTP/2.0 200 OK\n\n{}")
            sys.exit(1 if missing else 0)
        print(os.environ.get("GH_REMOTE_REF", os.environ["SOURCE_REF"]))
    elif "releases/tags/" in endpoint:
        tag = endpoint.rsplit("/", 1)[1]
        exists = (remote / tag).is_dir()
        if "--include" in args:
            print("HTTP/2.0 200 OK\n\n{}" if exists else "HTTP/2.0 404 Not Found\n\n{}")
            sys.exit(0 if exists else 1)
        print("43" if tag.startswith("desktop-") and tag == "desktop-" + os.environ["CHANNEL"] else "42")
    elif "/assets?" in endpoint:
        tag = "desktop-" + os.environ["CHANNEL"] if "/43/" in endpoint else os.environ["RELEASE_TAG"]
        for asset in sorted((remote / tag).iterdir()):
            print(asset.name)
    sys.exit(0)
if args[:2] == ["release", "view"]:
    directory = remote / args[2]
    if not directory.is_dir():
        sys.exit(1)
    if "--json" in args:
        for asset in sorted(directory.iterdir()):
            print(asset.name)
    sys.exit(0)
if args[:2] == ["release", "create"]:
    (remote / args[2]).mkdir()
    sys.exit(0)
if args[:2] == ["release", "upload"]:
    source = Path(args[3])
    target = remote / args[2] / source.name
    if os.environ.get("GH_FAIL") == "upload:" + source.name:
        sys.exit(17)
    if "--clobber" in args and os.environ.get("GH_FAIL") == "clobber:" + source.name:
        if target.exists():
            target.unlink()
        sys.exit(19)
    if target.exists() and "--clobber" not in args:
        sys.exit(1)
    shutil.copyfile(source, target)
    if os.environ.get("GH_CORRUPT") == source.name:
        target.write_bytes(b"corrupted remote asset")
    sys.exit(0)
if args[:2] == ["release", "download"]:
    name = args[args.index("--pattern") + 1]
    if os.environ.get("GH_FAIL") == "download:" + name:
        sys.exit(18)
    directory = Path(args[args.index("--dir") + 1])
    shutil.copyfile(remote / args[2] / name, directory / name)
    sys.exit(0)
raise SystemExit("unexpected fake gh command: " + " ".join(args))
'''


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
        identity = workflow.split("\n  identity:\n", 1)[1].split("\n  build-macos:\n", 1)[0]
        self.assertIn("ref: ${{ github.sha }}", identity)
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
            "needs: [identity, build-macos, build-windows, validate, windows-desktop-tests]", publish
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
            .replace("needs.windows-desktop-tests.result", "native_test_result")
            .replace("needs.identity.outputs.channel", "channel")
        )

        def publish_after(
            channel,
            identity_result="success",
            darwin_result="success",
            windows_result="skipped",
            validate_result="success",
            native_test_result="success",
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
                    "native_test_result": native_test_result,
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
        for channel in ("main", "stable"):
            for status in ("failure", "cancelled", "skipped"):
                self.assertFalse(publish_after(channel, windows_result="success", native_test_result=status))

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

    def _upload_fixture(self, channel, version, tag, include_windows):
        bash = self._working_bash()
        script = self._upload_step_script()
        case_dir = self.root / f"upload-{channel}-{int(include_windows)}-{tag}"
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
        gh.write_text('#!/bin/sh\nexec python "$GH_FAKE_SCRIPT" "$@"\n', encoding="utf-8")
        gh.chmod(0o755)
        fake_script = case_dir / "fake-gh.py"
        fake_script.write_text(FAKE_GH, encoding="utf-8")
        remote = case_dir / "remote"
        (remote / tag).mkdir(parents=True)
        (remote / f"desktop-{channel}").mkdir()
        scripts = case_dir / "scripts"
        scripts.mkdir()
        shutil.copy2(Path(feed.__file__), scripts / "desktop_update_feed.py")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_LOG": str(gh_log),
            "GH_FAKE_SCRIPT": str(fake_script),
            "GH_REMOTE": str(remote),
            "GITHUB_REPOSITORY": "loopx-project/loopx",
            "RELEASE_TAG": tag,
            "SOURCE_REF": "a" * 40,
            "EVENT_NAME": "release",
            "CHANNEL": channel,
            "VERSION": version,
        }
        return bash, script, case_dir, env, gh_log, remote

    def _execute_upload(self, fixture):
        bash, script, case_dir, env, _, _ = fixture
        return subprocess.run(
            [bash, "-c", script], cwd=case_dir, env=env, check=False,
            capture_output=True, text=True, timeout=30,
        )

    def _run_upload_step(self, channel, version, tag, include_windows):
        fixture = self._upload_fixture(channel, version, tag, include_windows)
        _, _, _, _, gh_log, _ = fixture
        result = self._execute_upload(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        return gh_log.read_text(encoding="utf-8").splitlines()

    def test_interrupted_upload_resumes_without_replacing_assets(self):
        fixture = self._upload_fixture("stable", "1.2.4", "v1.2.4", False)
        _, _, case_dir, env, log, remote = fixture
        env["GH_FAIL"] = "upload:LoopX.app.tar.gz.sig"
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        release = remote / "v1.2.4"
        self.assertEqual({path.name for path in release.iterdir()}, {"LoopX.app.tar.gz"})
        self.assertEqual(list((remote / "desktop-stable").iterdir()), [])
        del env["GH_FAIL"]
        resumed = self._execute_upload(fixture)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        again = self._execute_upload(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        for local in (case_dir / "dist/updater").iterdir():
            self.assertEqual((release / local.name).read_bytes(), local.read_bytes())
        self.assertEqual(
            (remote / "desktop-stable/desktop-updater.json").read_bytes(),
            (release / "desktop-updater.json").read_bytes(),
        )
        calls = log.read_text(encoding="utf-8").splitlines()
        tag_uploads = [line for line in calls if line.startswith("release upload v1.2.4 ")]
        self.assertFalse(any("--clobber" in line for line in tag_uploads))
        pointer_upload = "release upload desktop-stable dist/updater/desktop-updater.json --clobber"
        self.assertEqual(calls.count(pointer_upload), 1)
        self.assertLess(
            calls.index("release upload v1.2.4 dist/updater/desktop-updater.json"),
            calls.index(pointer_upload),
        )

    def test_mismatched_existing_asset_aborts_before_any_upload(self):
        fixture = self._upload_fixture("stable", "1.2.4", "v1.2.4", False)
        _, _, _, _, log, remote = fixture
        existing = remote / "v1.2.4/LoopX.app.tar.gz.sig"
        existing.write_bytes(b"different signature")
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        self.assertEqual(existing.read_bytes(), b"different signature")
        self.assertEqual({p.name for p in existing.parent.iterdir()}, {existing.name})
        self.assertFalse(any(line.startswith("release upload ") for line in log.read_text().splitlines()))

    def test_wrong_release_commit_or_failed_readback_never_advances_pointer(self):
        for failure in ("wrong-commit", "download"):
            with self.subTest(failure=failure):
                # Each scenario has an independent remote release.
                if failure == "wrong-commit":
                    version, tag = "1.2.4", "v1.2.4"
                else:
                    version, tag = "1.2.5", "v1.2.5"
                fixture = self._upload_fixture("stable", version, tag, False)
                _, _, _, env, _, remote = fixture
                if failure == "wrong-commit":
                    env["GH_REMOTE_REF"] = "b" * 40
                else:
                    env["GH_FAIL"] = "download:LoopX.app.tar.gz"
                self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
                self.assertEqual(list((remote / "desktop-stable").iterdir()), [])
                self.assertFalse((remote / tag / "desktop-updater.json").exists())

    def test_preview_release_creation_resumes_and_api_failure_never_creates(self):
        fixture = self._upload_fixture("main", "0.0.0-main.123.1", "desktop-main-123-1", True)
        _, _, _, env, log, remote = fixture
        (remote / env["RELEASE_TAG"]).rmdir()
        env["EVENT_NAME"] = "workflow_dispatch"
        env["GH_FAIL"] = "api:unavailable"
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        self.assertFalse((remote / env["RELEASE_TAG"]).exists())
        del env["GH_FAIL"]
        env["GH_REMOTE_REF"] = "b" * 40
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        self.assertFalse((remote / env["RELEASE_TAG"]).exists())
        del env["GH_REMOTE_REF"]
        env["GH_TAG_MISSING"] = "1"
        env["GH_FAIL"] = "upload:LoopX.app.tar.gz.sig"
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        del env["GH_FAIL"]
        result = self._execute_upload(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        creates = [line for line in log.read_text().splitlines() if line.startswith("release create ")]
        self.assertEqual(len(creates), 1)

    def test_conflicting_feed_and_case_collision_are_rejected_before_upload(self):
        for version, name in (("1.2.4", "desktop-updater.json"), ("1.2.5", "loopx.app.tar.gz")):
            with self.subTest(name=name):
                tag = "v" + version
                fixture = self._upload_fixture("stable", version, tag, False)
                _, _, _, _, log, remote = fixture
                (remote / tag / name).write_bytes(b"conflict")
                self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
                self.assertEqual({p.name for p in (remote / tag).iterdir()}, {name})
                self.assertFalse(any(line.startswith("release upload ") for line in log.read_text().splitlines()))

    def test_wrong_feed_or_corrupt_remote_upload_never_publishes_a_pointer(self):
        for version, failure in (("1.2.4", "feed"), ("1.2.5", "corrupt")):
            with self.subTest(failure=failure):
                fixture = self._upload_fixture("stable", version, "v" + version, False)
                _, _, case_dir, env, _, remote = fixture
                if failure == "feed":
                    (case_dir / "dist/updater/desktop-updater.json").write_text('{"version":"0.0.0"}')
                else:
                    env["GH_CORRUPT"] = "LoopX.app.tar.gz"
                self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
                self.assertEqual(list((remote / "desktop-stable").iterdir()), [])
                self.assertFalse((remote / ("v" + version) / "desktop-updater.json").exists())

    def test_newer_channel_pointer_survives_an_older_release_retry(self):
        fixture = self._upload_fixture("stable", "1.2.4", "v1.2.4", False)
        _, _, _, _, _, remote = fixture
        pointer = remote / "desktop-stable/desktop-updater.json"
        pointer.write_text('{"version":"1.2.5"}', encoding="utf-8")
        result = self._execute_upload(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pointer.read_text(encoding="utf-8"), '{"version":"1.2.5"}')

    def test_failed_pointer_replacement_recovers_from_complete_release(self):
        fixture = self._upload_fixture("stable", "1.2.4", "v1.2.4", False)
        _, _, _, env, _, remote = fixture
        pointer = remote / "desktop-stable/desktop-updater.json"
        pointer.write_text('{"version":"1.2.3"}', encoding="utf-8")
        env["GH_FAIL"] = "clobber:desktop-updater.json"
        self.assertNotEqual(self._execute_upload(fixture).returncode, 0)
        self.assertFalse(pointer.exists())
        feed_path = remote / "v1.2.4/desktop-updater.json"
        self.assertTrue(feed_path.is_file())
        del env["GH_FAIL"]
        result = self._execute_upload(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pointer.read_bytes(), feed_path.read_bytes())

    def test_upload_step_skips_absent_windows_artifact_on_stable(self):
        stable = self._run_upload_step("stable", "1.2.4", "v1.2.4", False)
        self.assertIn("release upload v1.2.4 dist/updater/LoopX.app.tar.gz", stable)
        self.assertIn(
            "release upload v1.2.4 dist/updater/LoopX.app.tar.gz.sig", stable
        )
        self.assertIn(
            "release upload v1.2.4 dist/updater/desktop-updater.json",
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
            if line.startswith("release upload desktop-main-123-1 dist/updater/desktop-updater.json")
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
