#!/usr/bin/env python3
"""Embed an exact public Git snapshot in the signed desktop distribution.

Never collect a developer's whole working directory or private local state.
"""

from __future__ import annotations

import json
from pathlib import Path
import gzip
import subprocess
import sys
import tarfile
import io
import importlib.util
import tomllib
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loopx.cli_commands.update_artifact import extract_artifact, file_hash, validate_default_contract
from loopx.file_lock import exclusive_file_lock
from scripts.qualify_runtime_artifact import observe_default_contract


_ARCHIVE_NAME = "runtime-source.tar.gz"
_PREVIOUS_ARCHIVE_NAME = ".runtime-source.previous.tar.gz"


def build(root: Path) -> None:
    destination = root / "apps/desktop/loopx-control-plane/runtime"
    destination.mkdir(parents=True, exist_ok=True)
    publish_lock = root / ".local/desktop-runtime-bundle"
    previous_archive = destination / _PREVIOUS_ARCHIVE_NAME
    if previous_archive.exists() or previous_archive.is_symlink():
        with exclusive_file_lock(publish_lock, operation="desktop-runtime-publish"):
            _recover_previous_bundle(destination)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    subprocess.run(
        [sys.executable, str(root / "scripts/chat_bundle.py"), "ensure"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(root / "scripts/chat_bundle.py"), "verify", "--source"],
        cwd=root,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix=".runtime-candidate-", dir=destination) as raw:
        staging = Path(raw)
        archive = _build_archive(root, revision, staging)
        entries = qualify_archive(archive)
        # Probe the same archived writer the signed App will install before
        # replacing either part of the previous Desktop bundle.
        with tempfile.TemporaryDirectory(prefix="loopx-desktop-contract-") as raw_source:
            source = extract_artifact(archive, Path(raw_source).resolve())
            default_contract = validate_default_contract(observe_default_contract(source))
        identity = {
            "schema_version": "desktop_runtime_bundle_v1",
            "source_revision": revision,
            "package_version": archive_package_version(archive),
            "default_contract": default_contract,
            "sha256": file_hash(archive),
        }
        identity_file = staging / "identity.json"
        identity_file.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
        with exclusive_file_lock(publish_lock, operation="desktop-runtime-publish"):
            _publish_bundle(destination, archive, identity_file)
    print(
        f"Prepared exact desktop runtime: {revision[:12]} ({entries} installable entries)"
    )


def _build_archive(root: Path, revision: str, staging: Path) -> Path:
    archive = staging / _ARCHIVE_NAME
    subprocess.run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            f"--output={archive}",
            revision,
            "loopx",
            "scripts",
            "skills",
            "docs",
            "man",
            "examples",
            "apps/presentation",
            ".github",
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "setup.py",
            "MANIFEST.in",
        ],
        cwd=root,
        check=True,
    )
    # Git carries only source; append the verified frontend to the exact archive.
    bundle_root = root / "loopx/web/chat"
    manifest = json.loads((bundle_root / "bundle-manifest.json").read_text())
    if manifest["source_revision"] != revision:
        raise RuntimeError(
            "frontend build belongs to another source revision; rebuild before desktop packaging"
        )
    # The contract delegates its digest shape to the shared owner; this build runs from a
    # checkout, where LoopX may not be installed, so the root has to be importable first.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "chat_contract", root / "loopx/presentation/chat_bundle.py"
    )
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    for name in manifest["source_files"]:
        committed = subprocess.check_output(
            ["git", "show", f"{revision}:{name}"], cwd=root
        )
        if (
            contract.source_digest(Path(name), committed)
            != manifest["source_files"][name]
        ):
            raise RuntimeError(
                f"desktop build input differs from committed source: {name}"
            )
    rebuilt = staging / "runtime-source.staged.tar.gz"
    with (
        tarfile.open(archive, "r:gz") as source,
        tarfile.open(rebuilt, "w:gz", format=tarfile.PAX_FORMAT) as target,
    ):
        for member in source.getmembers():
            if member.name.startswith("loopx/web/chat/"):
                continue
            target.addfile(
                member, source.extractfile(member) if member.isfile() else None
            )
        for path in sorted(bundle_root.rglob("*")):
            if not path.is_file():
                continue
            member = tarfile.TarInfo(path.relative_to(root).as_posix())
            content = path.read_bytes()
            member.size = len(content)
            member.mode = 0o644
            target.addfile(member, io.BytesIO(content))
    rebuilt.replace(archive)
    return archive


def _recover_previous_bundle(destination: Path) -> None:
    """Recover an interrupted publication under the existing publish lock."""
    previous_archive = destination / _PREVIOUS_ARCHIVE_NAME
    if not previous_archive.exists() and not previous_archive.is_symlink():
        return
    target_archive = destination / _ARCHIVE_NAME
    identity = destination / "identity.json"
    try:
        expected_digest = json.loads(identity.read_text(encoding="utf-8"))["sha256"]
    except (KeyError, TypeError, ValueError, FileNotFoundError) as error:
        raise RuntimeError(f"Cannot recover Desktop runtime from its identity; preserved {previous_archive}") from error
    if target_archive.is_file() and file_hash(target_archive) == expected_digest:
        previous_archive.unlink()
    elif previous_archive.is_file() and file_hash(previous_archive) == expected_digest:
        previous_archive.replace(target_archive)
    else:
        raise RuntimeError(f"No Desktop runtime archive matches its identity; preserved {previous_archive}")


def _publish_bundle(destination: Path, archive: Path, identity: Path) -> None:
    _recover_previous_bundle(destination)
    target_archive = destination / _ARCHIVE_NAME
    previous_archive = destination / _PREVIOUS_ARCHIVE_NAME
    had_previous = target_archive.is_file() or target_archive.is_symlink()
    try:
        if had_previous:
            target_archive.rename(previous_archive)
        archive.replace(target_archive)
        identity.replace(destination / identity.name)
    except (OSError, KeyboardInterrupt):
        # The atomic identity move commits the new pair. Until that move, the
        # previous archive stays available even if staging cleanup runs.
        if not identity.exists():
            previous_archive.unlink(missing_ok=True)
        elif previous_archive.exists() or previous_archive.is_symlink():
            previous_archive.replace(target_archive)
        elif not had_previous and not archive.exists():
            target_archive.unlink(missing_ok=True)
        raise
    else:
        previous_archive.unlink(missing_ok=True)


def archive_package_version(archive: Path) -> str:
    """Use the archived wheel metadata, not the shell tag or working tree."""
    with tarfile.open(archive, "r:gz") as source:
        metadata = source.extractfile("pyproject.toml")
        if metadata is None:
            raise RuntimeError("runtime archive has no package metadata")
        version = tomllib.loads(metadata.read().decode("utf-8"))["project"]["version"]
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("runtime archive has no package version")
    return version


def qualify_archive(archive: Path) -> int:
    """Fail the release build when the App-side extractor could never install it.

    The Rust extractor iterates logical entries with the pinned tar crate's
    non-raw iterator: PAX local headers ('x', emitted by ``git archive`` for
    paths beyond ustar capacity), PAX global headers ('g') and GNU
    longname/longlink records ('L'/'K') carry path metadata for the entry that
    follows and are folded away before the App's ``is_file``/``is_dir`` checks
    run. The gate must therefore accept those metadata carriers and reject only
    entries that survive as real links, devices or reserved types -- shapes
    every client would fail the post-restart install (and repair) with
    ``runtime_bundle_invalid`` while the feed keeps advertising the update.
    Raw typeflag bytes are inspected precisely because tar readers silently
    fold extended headers; see bundled_runtime.rs tests for the matching
    extractor-side acceptance matrix.
    """
    metadata_typeflags = (b"g", b"x", b"L", b"K")
    entries = 0
    with gzip.open(archive, "rb") as bundle:
        while True:
            header = bundle.read(512)
            if len(header) < 512 or header.count(0) == 512:
                break
            typeflag = header[156:157]
            name = header[:100].rstrip(b"\0").decode("utf-8", "replace")
            if (
                typeflag not in (b"0", b"\0", b"5")
                and typeflag not in metadata_typeflags
            ):
                raise SystemExit(
                    f"runtime bundle entry is neither a file nor a directory, "
                    f"which the App cannot install: {name!r} "
                    f"(tar typeflag {typeflag!r})"
                )
            if typeflag in (b"0", b"\0", b"5"):
                entries += 1
            size = int(
                (header[124:136].rstrip(b"\0 ") or b"0").decode("ascii") or "0", 8
            )
            bundle.read((size + 511) // 512 * 512)
    return entries


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1])
