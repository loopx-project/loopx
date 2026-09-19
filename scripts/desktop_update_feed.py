#!/usr/bin/env python3
"""Publish a complete native updater feed only after all signed artifacts exist."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote


def release_channel(tag: str, prerelease: bool) -> str:
    """Only core stable releases may advance the desktop stable pointer."""
    return "stable" if not prerelease and re.fullmatch(r"v\d+\.\d+\.\d+", tag) else "skip"


def build(directory: Path, version: str, tag: str) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", version):
        raise ValueError("invalid desktop version")
    if not re.fullmatch(r"[0-9A-Za-z._-]+", tag):
        raise ValueError("invalid release tag")
    platforms = {}
    # Publish only platforms with a qualified bundled-runtime installer.
    for platform, suffix in (("darwin-aarch64", ".app.tar.gz"),):
        matches = list(directory.glob(f"*{suffix}"))
        if len(matches) != 1 or matches[0].stat().st_size == 0:
            raise ValueError(f"missing or ambiguous artifact: {platform}")
        artifact = matches[0]
        signature = artifact.with_name(artifact.name + ".sig").read_text().strip()
        if not signature:
            raise ValueError(f"missing signature: {platform}")
        platforms[platform] = {
            "url": f"https://github.com/loopx-project/loopx/releases/download/{quote(tag)}/{quote(artifact.name)}",
            "signature": signature,
        }
    return {"version": version, "notes": "LoopX App and matching bundled runtime.", "platforms": platforms}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--tag")
    parser.add_argument("--classify-release")
    parser.add_argument("--prerelease", choices=("true", "false"), default="false")
    args = parser.parse_args()
    if args.classify_release is not None:
        print(release_channel(args.classify_release, args.prerelease == "true"))
        raise SystemExit(0)
    if args.directory is None or args.version is None or args.tag is None:
        parser.error("--directory, --version and --tag are required to build a feed")
    output = build(args.directory, args.version, args.tag)
    (args.directory / "desktop-updater.json").write_text(json.dumps(output, indent=2) + "\n")
