#!/usr/bin/env python3
"""Build a source-pinned, reproducible LoopX self-hosting bundle."""

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


TEMPLATE = Path(__file__).resolve().parent
REPOSITORY = TEMPLATE.parents[2]
BUNDLE_NAME = "loopx-remote-authority-compose"
SOURCE_PATHS = (
    "LICENSE",
    "NOTICE",
    "package.json",
    "package-lock.json",
    "examples/coordination/remote-authority-client.ts",
    "examples/coordination/remote-authority-service.ts",
    "loopx/control_plane",
)
BUNDLE_FILES = (
    ".dockerignore",
    "Dockerfile",
    "README.en.md",
    "README.md",
    "client.sh",
    "compose.yaml",
    "launch.mjs",
    "provision.mjs",
    "start.sh",
    "state.sh",
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPOSITORY)


def gzip_archive(data: bytes) -> bytes:
    encoded = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=encoded, mode="wb", mtime=0) as compressed:
        compressed.write(data)
    return encoded.getvalue()


def bundle(revision: str) -> bytes:
    source_tar = git("archive", "--format=tar", revision, "--", *SOURCE_PATHS)
    source_archive = gzip_archive(source_tar)
    source_digest = hashlib.sha256(source_archive).hexdigest()
    files = {name: (TEMPLATE / name).read_bytes() for name in BUNDLE_FILES}
    image_id = hashlib.sha256(b"".join(
        hashlib.sha256(data).digest()
        for data in (source_archive, files["Dockerfile"], files["compose.yaml"],
                     files["provision.mjs"], files["launch.mjs"])
    )).hexdigest()
    files[".env"] = (
        f"LOOPX_REVISION={revision}\nLOOPX_SOURCE_SHA256={source_digest}\n"
        f"LOOPX_IMAGE_ID={image_id}\n"
    ).encode("ascii")
    files["loopx-source.tar.gz"] = source_archive
    for name in ("LICENSE", "NOTICE"):
        files[name] = git("show", f"{revision}:{name}")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as output:
        for name, contents in sorted(files.items()):
            entry = tarfile.TarInfo(f"{BUNDLE_NAME}/{name}")
            entry.size = len(contents)
            entry.mode = 0o755 if name.endswith(".sh") else 0o644
            output.addfile(entry, io.BytesIO(contents))
    return gzip_archive(archive.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True, help="full LoopX Git commit")
    parser.add_argument("--output", type=Path, required=True, help="new tar.gz file")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        parser.error("--source-revision must be a full lowercase Git commit SHA")
    try:
        if git("cat-file", "-t", args.source_revision).strip() != b"commit":
            parser.error("--source-revision must identify a commit")
        contents = bundle(args.source_revision)
        with tempfile.TemporaryDirectory(dir=args.output.parent) as work:
            temporary = Path(work) / "bundle.tar.gz"
            temporary.write_bytes(contents)
            os.link(temporary, args.output)
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Could not build bundle: {error}\n")
    print(f"{hashlib.sha256(contents).hexdigest()}  {args.output}")


if __name__ == "__main__":
    main()
