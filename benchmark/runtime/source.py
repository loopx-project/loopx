"""Pinned product and research-runner archives; neither includes local state."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def checked_revision(source: Path, expected: str | None = None) -> str:
    head = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if expected and head != expected:
        raise RuntimeError("Source does not match its expected revision")
    subprocess.run(
        ["git", "-C", str(source), "diff", "HEAD", "--exit-code", "--quiet"],
        check=True,
    )
    return head


def archive_source(source: Path, revision: str, output: Path, paths=()) -> None:
    with output.open("wb") as stream:
        subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", revision, *paths],
            stdout=stream, check=True, timeout=120,
        )
    # A complete source install needs the packaged frontend. Carry a prepared
    # delivery through the same source-freshness contract as the installer,
    # rather than making each trial download and build it again. Partial runner
    # overlays and plain workers must remain independent of product assets.
    bundle = source / "loopx/web/chat"
    if paths or not (bundle.exists() or bundle.is_symlink()):
        return
    from loopx.presentation.chat_bundle import MANIFEST, validate_bundle

    if bundle.is_symlink():
        raise RuntimeError("Prepared Chat bundle must not be a symlink")
    manifest = validate_bundle(bundle, source_root=source)
    tracked = set(subprocess.check_output(
        ["git", "-C", str(source), "ls-tree", "-r", "--name-only", revision],
        text=True,
    ).splitlines())
    if not set(manifest["source_files"]) <= tracked:
        raise RuntimeError("Prepared Chat bundle includes uncommitted source inputs")
    contents = {}
    for name, digest in manifest["files"].items():
        data = (bundle / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError("Prepared Chat bundle changed during archive creation")
        contents[name] = data
    contents[MANIFEST] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    # Generated files are outside Git; ensure their validated source inputs
    # still refer to the revision archived above before admitting them.
    checked_revision(source, revision)
    with tarfile.open(output, "a") as archive:
        for name, data in sorted(contents.items()):
            member = tarfile.TarInfo(f"loopx/web/chat/{name}")
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))


def source_pins(source: Path, runner: Path, expected: str | None,
                expected_runner: str | None) -> tuple[str, str]:
    if source != runner and (not expected or not expected_runner):
        raise ValueError("Separate product and runner sources require both revision pins")
    head = checked_revision(source, expected)
    return head, head if source == runner else checked_revision(runner, expected_runner)
