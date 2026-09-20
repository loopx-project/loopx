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
