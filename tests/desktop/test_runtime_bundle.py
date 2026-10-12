"""Reject runtime snapshots the App-side installer could never unpack."""
import gzip
import io
import importlib.util
from pathlib import Path
import tempfile
import unittest
import tarfile

spec = importlib.util.spec_from_file_location(
    "desktop_bundle", Path(__file__).resolve().parents[2] / "scripts/desktop_runtime_bundle.py"
)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def tar_header(name: bytes, typeflag: bytes, size: int = 0) -> bytes:
    """One 512-byte ustar header with a checksum, ready for the raw scanner."""
    header = bytearray(512)
    header[:len(name)] = name
    header[156:157] = typeflag
    header[124:136] = f"{size:011o}\0".encode()
    header[148:156] = b"        "
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\0 ".encode()
    return bytes(header)


class ArchiveQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_archive(self, *blocks: bytes) -> Path:
        archive = self.root / "runtime-source.tar.gz"
        with gzip.open(archive, "wb") as compressed:
            for block in blocks:
                compressed.write(block)
        return archive

    def test_core_version_comes_from_archived_wheel_metadata(self):
        archive = self.root / "versioned.tar.gz"
        content = b'[project]\nversion = "1.5.2"\n'
        with tarfile.open(archive, "w:gz") as target:
            member = tarfile.TarInfo("pyproject.toml")
            member.size = len(content)
            target.addfile(member, io.BytesIO(content))
        # Neither a newer working-tree version nor a Main shell tag owns this
        # archive's package identity.
        (self.root / "pyproject.toml").write_text('[project]\nversion = "1.6.0"\n')
        self.assertEqual(bundle.archive_package_version(archive), "1.5.2")

    def test_invalid_archived_package_version_is_rejected(self):
        for version in ["42", '""', '"   "']:
            archive = self.root / "invalid-version.tar.gz"
            content = f"[project]\nversion = {version}\n".encode()
            with tarfile.open(archive, "w:gz") as target:
                member = tarfile.TarInfo("pyproject.toml")
                member.size = len(content)
                target.addfile(member, io.BytesIO(content))
            with self.assertRaises(RuntimeError):
                bundle.archive_package_version(archive)

    def test_files_and_directories_qualify(self):
        archive = self.write_archive(
            tar_header(b"loopx/", b"5"),
            tar_header(b"loopx/cli.py", b"0"),
            b"\0" * 1024,
        )
        self.assertEqual(bundle.qualify_archive(archive), 2)

    def test_symlinks_fail_the_release_build(self):
        archive = self.write_archive(
            tar_header(b"scripts/link", b"2"),
            b"\0" * 1024,
        )
        with self.assertRaises(SystemExit) as rejected:
            bundle.qualify_archive(archive)
        self.assertIn("scripts/link", str(rejected.exception))

    def test_pax_local_headers_carry_long_paths_and_qualify(self):
        # `git archive` emits a PAX local header ('x') whenever a path exceeds
        # ustar capacity. The pinned tar crate's non-raw iterator folds it into
        # the entry that follows, so the App installs the snapshot fine and the
        # build gate must accept the same shape (and not count it as an entry).
        archive = self.write_archive(
            tar_header(b"docs/deep-path.md", b"x", size=512),
            b"21 path=docs/deep-path.md\n".ljust(512, b"\0"),
            tar_header(b"docs/deep-path.md", b"0"),
            b"\0" * 1024,
        )
        self.assertEqual(bundle.qualify_archive(archive), 1)

    def test_gnu_longname_headers_carry_long_paths_and_qualify(self):
        # GNU longname records ('L') are the other path-metadata carrier the
        # non-raw iterator folds away; accept them like the App does.
        archive = self.write_archive(
            tar_header(b"././@LongLink", b"L", size=512),
            b"docs/deep-path.md".ljust(512, b"\0"),
            tar_header(b"docs/deep-path.md", b"0"),
            b"\0" * 1024,
        )
        self.assertEqual(bundle.qualify_archive(archive), 1)

    def test_metadata_headers_do_not_smuggle_links_through(self):
        # Accepting path-metadata headers must not weaken the link rejection:
        # the entry that follows the metadata is still type-checked.
        archive = self.write_archive(
            tar_header(b"docs/deep-path.md", b"x", size=512),
            b"21 path=docs/deep-path.md\n".ljust(512, b"\0"),
            tar_header(b"scripts/link", b"2"),
            b"\0" * 1024,
        )
        with self.assertRaises(SystemExit) as rejected:
            bundle.qualify_archive(archive)
        self.assertIn("scripts/link", str(rejected.exception))

    def test_real_git_long_path_archive_qualifies(self):
        # Mirror of the reviewer's synthetic reproduction: a real
        # `git archive` of a snapshot whose path exceeds ustar capacity emits
        # PAX local headers that the gate must not reject.
        import subprocess

        repo = self.root / "repo"
        deep = repo / ("docs/" + "very-deep-directory-name/" * 6 + "long-path.md")
        deep.parent.mkdir(parents=True)
        deep.write_text("# beyond ustar capacity\n")

        def git(*args: str) -> None:
            subprocess.run(
                ["git", *args], cwd=repo, check=True, capture_output=True, text=True
            )

        git("init", "-q")
        git("config", "user.email", "build-gate@example.invalid")
        git("config", "user.name", "build gate")
        git("add", ".")
        git("commit", "-qm", "long-path snapshot")
        archive = self.root / "real-runtime-source.tar.gz"
        subprocess.run(
            [
                "git", "archive", "--format=tar.gz",
                f"--output={archive}", "HEAD",
            ],
            cwd=repo,
            check=True,
        )
        self.assertGreaterEqual(bundle.qualify_archive(archive), 1)

    def test_pax_global_headers_are_skipped_like_the_app_extractor(self):
        archive = self.write_archive(
            tar_header(b"pax_global_header", b"g", size=512),
            b"52 comment=qualify desktop runtime\n".ljust(512, b"\0"),
            tar_header(b"loopx/cli.py", b"0"),
            b"\0" * 1024,
        )
        self.assertEqual(bundle.qualify_archive(archive), 1)


if __name__ == "__main__":
    unittest.main()
