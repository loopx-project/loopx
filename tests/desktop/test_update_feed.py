"""Fail closed before advertising incomplete updates."""
import importlib.util
from pathlib import Path
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

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / "LoopX.app.tar.gz"
        self.artifact.write_bytes(b"test archive")
        self.signature = self.root / "LoopX.app.tar.gz.sig"
        self.signature.write_text("test signature")

    def build(self):
        return feed.build(self.root, "0.0.0-main.123.1", "desktop-main-123-1")

    def test_immutable_source_and_qualified_platform(self):
        result = self.build()
        self.assertEqual(set(result["platforms"]), {"darwin-aarch64"})
        self.assertEqual(result["platforms"]["darwin-aarch64"]["url"],
                         "https://github.com/loopx-project/loopx/releases/download/desktop-main-123-1/LoopX.app.tar.gz")

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

    def test_untrusted_version_or_tag(self):
        for version, tag in [("v1.0.0", "valid"), ("1.0.0", "../main"), ("1.0.0;command", "main")]:
            with self.subTest(version=version, tag=tag):
                with self.assertRaises(ValueError):
                    feed.build(self.root, version, tag)
