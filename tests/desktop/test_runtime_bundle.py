"""Reject runtime snapshots the App-side installer could never unpack."""
import gzip
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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


class RuntimeBundleBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture_files = {
            "loopx/presentation/chat_bundle.py": "# Synthetic frontend contract\n",
            "scripts/chat_bundle.py": "# Synthetic frontend preparation\n",
            "scripts/install-local.sh": "#!/bin/sh\n",
            "skills/synthetic.txt": "skills\n",
            "docs/synthetic.txt": "docs\n",
            "man/synthetic.txt": "man\n",
            "examples/synthetic.txt": "examples\n",
            "apps/presentation/synthetic.txt": "presentation\n",
            ".github/synthetic.txt": "workflows\n",
            "README.md": "Synthetic runtime\n",
            "LICENSE": "license\n",
            "pyproject.toml": '[project]\nversion = "1.2.3"\n',
            "setup.py": "# Synthetic setup\n",
            "MANIFEST.in": "# Synthetic manifest\n",
        }
        for name, content in fixture_files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Fixture Author",
            "GIT_COMMITTER_NAME": "Fixture Author",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        for args in (("init", "--quiet"), ("add", "--", *fixture_files),
                     ("-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "Synthetic runtime")):
            subprocess.run(["git", *args], cwd=self.root, env=env, check=True, capture_output=True)
        self.revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        chat = self.root / "loopx/web/chat"
        chat.mkdir(parents=True)
        (chat / "bundle-manifest.json").write_text(
            json.dumps({"source_revision": self.revision, "source_files": {}}),
            encoding="utf-8",
        )

    def previous_outputs(self):
        destination = self.root / "apps/desktop/loopx-control-plane/runtime"
        destination.mkdir(parents=True)
        archive = destination / "runtime-source.tar.gz"
        identity = destination / "identity.json"
        archive.write_bytes(b"previous archive")
        identity.write_bytes(b"previous identity")
        return archive, identity

    def test_unqualified_default_keeps_previous_archive_and_identity(self):
        archive, identity = self.previous_outputs()
        with patch.object(bundle, "observe_default_contract", return_value={"surface": "unsupported"}):
            with self.assertRaisesRegex(ValueError, "unqualified for public adoption"):
                bundle.build(self.root)

        self.assertEqual(archive.read_bytes(), b"previous archive")
        self.assertEqual(identity.read_bytes(), b"previous identity")
        self.assertEqual(set(archive.parent.iterdir()), {archive, identity})

    def test_supported_default_publishes_identity_for_exact_archive(self):
        from loopx.cli_commands.update_artifact import PUBLIC_DEFAULT_CONTRACT

        archive, identity_file = self.previous_outputs()
        (self.root / "README.md").write_text("Uncommitted source\n", encoding="utf-8")

        def observe(source: Path):
            self.assertEqual((source / "README.md").read_text(), "Synthetic runtime\n")
            return PUBLIC_DEFAULT_CONTRACT

        with patch.object(bundle, "observe_default_contract", side_effect=observe):
            bundle.build(self.root)

        identity = json.loads(identity_file.read_text())
        self.assertEqual(identity["source_revision"], self.revision)
        self.assertEqual(identity["package_version"], "1.2.3")
        self.assertEqual(identity["default_contract"], PUBLIC_DEFAULT_CONTRACT)
        self.assertEqual(identity["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_identity_replacement_failure_restores_previous_archive(self):
        from loopx.cli_commands.update_artifact import PUBLIC_DEFAULT_CONTRACT

        archive, identity = self.previous_outputs()
        real_replace = Path.replace

        def replace(path: Path, target: Path):
            if target == identity:
                raise OSError("synthetic identity replacement failure")
            return real_replace(path, target)

        with (
            patch.object(bundle, "observe_default_contract", return_value=PUBLIC_DEFAULT_CONTRACT),
            patch.object(Path, "replace", replace),
            self.assertRaisesRegex(OSError, "synthetic identity replacement failure"),
        ):
            bundle.build(self.root)

        self.assertEqual(archive.read_bytes(), b"previous archive")
        self.assertEqual(identity.read_bytes(), b"previous identity")
        self.assertEqual(set(archive.parent.iterdir()), {archive, identity})


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
