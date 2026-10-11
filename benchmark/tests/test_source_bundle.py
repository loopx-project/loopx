"""Pinned archives admit prepared assets, never arbitrary ignored host files."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from benchmark.runtime.source import archive_source, checked_revision
from loopx.presentation.chat_bundle import MANIFEST, source_inputs, validate_bundle


@pytest.fixture
def prepared_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for args in [("init",), ("config", "user.name", "Fixture"),
                 ("config", "user.email", "fixture@example.invalid")]:
        subprocess.run(["git", "-C", str(source), *args], check=True,
                       capture_output=True)
    package = source / "apps/presentation/dashboard/package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}")
    (source / ".gitignore").write_text("loopx/web/chat/\nprivate/\n")
    subprocess.run(["git", "-C", str(source), "add", "apps", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "source"], check=True,
                   capture_output=True)
    revision = checked_revision(source)
    bundle = source / "loopx/web/chat"
    (bundle / "assets").mkdir(parents=True)
    files = {"index.html": '<script src="/chat/assets/main.js"></script>',
             "manifest.webmanifest": "{}", "assets/main.js": "export const ready = true;",
             "asset-retention.json": json.dumps({"schema_version": "loopx_chat_asset_retention_v1",
                                                "generations": [["assets/main.js"]]})}
    for name, data in files.items():
        (bundle / name).write_text(data)
    (bundle / MANIFEST).write_text(json.dumps({
        "schema_version": "loopx_chat_bundle_v1", "source_revision": revision,
        "source_files": source_inputs(source), "current_assets": ["assets/main.js"],
        "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
    }))
    (source / "private").mkdir()
    (source / "private/notes.txt").write_text("not an archive input")
    return source, revision, bundle


def test_complete_archive_preserves_validated_delivery_without_private_files(prepared_source, tmp_path):
    source, revision, bundle = prepared_source
    output = tmp_path / "source.tar"
    archive_source(source, revision, output)
    extracted = tmp_path / "extracted"
    with tarfile.open(output) as archive:
        assert not any(name.startswith("private/") for name in archive.getnames())
        archive.extractall(extracted, filter="data")
    validate_bundle(extracted / "loopx/web/chat", source_root=extracted)
    assert (extracted / "loopx/web/chat/assets/main.js").read_bytes() == (bundle / "assets/main.js").read_bytes()


@pytest.mark.parametrize("problem", ["corrupt", "stale", "extra", "symlink", "root_symlink", "uncommitted_input"])
def test_invalid_prepared_delivery_fails_closed(prepared_source, tmp_path, problem):
    source, revision, bundle = prepared_source
    if problem == "corrupt":
        (bundle / "assets/main.js").write_text("corrupt")
    elif problem == "stale":
        (source / "apps/presentation/dashboard/package.json").write_text('{"changed": true}')
    elif problem == "extra":
        (bundle / "private.txt").write_text("not a declared asset")
    elif problem == "symlink":
        asset = bundle / "assets/main.js"
        asset.unlink()
        asset.symlink_to(source / "private/notes.txt")
    elif problem == "root_symlink":
        moved = bundle.rename(tmp_path / "bundle")
        bundle.symlink_to(moved, target_is_directory=True)
    else:
        untracked = source / "apps/presentation/dashboard/src/private.ts"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("export const privateInput = true;")
        witness = bundle / MANIFEST
        manifest = json.loads(witness.read_text())
        manifest["source_files"] = source_inputs(source)
        witness.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        archive_source(source, revision, tmp_path / "source.tar")


def test_partial_archive_ignores_even_invalid_product_assets(prepared_source, tmp_path):
    source, revision, bundle = prepared_source
    (bundle / "private.txt").write_text("invalid delivery")
    output = tmp_path / "partial.tar"
    archive_source(source, revision, output, ("apps",))
    with tarfile.open(output) as archive:
        assert all(name.startswith("apps") for name in archive.getnames())
