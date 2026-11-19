#!/usr/bin/env python3
"""Publish a complete native updater feed only after all signed artifacts exist."""
import argparse
import base64
import binascii
import json
import re
from pathlib import Path
from urllib.parse import quote


STABLE_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
MAIN_VERSION = re.compile(r"0\.0\.0-main\.(\d+)\.(\d+)")

# Publish only platforms with a qualified signed update artifact for the
# channel. Windows NSIS updates ship on the main channel first; the stable
# channel keeps manual Windows installs until that rollout is approved.
# Keys follow the updater's `{os}-{arch}-{installer}` lookup. The installer
# component comes from the bundle-type marker the NSIS install carries.
# Publish only the NSIS-specific Windows key until MSI installs are qualified.
# The `windows-x86_64` fallback matches every Windows installer kind.
QUALIFIED_PLATFORMS = {
    "main": (
        ("darwin-aarch64", ".app.tar.gz"),
        ("windows-x86_64-nsis", "-updater-setup.exe"),
    ),
    "stable": (("darwin-aarch64", ".app.tar.gz"),),
}


def release_channel(tag: str, prerelease: bool) -> str:
    """Only core stable releases may advance the desktop stable pointer."""
    return "stable" if not prerelease and re.fullmatch(r"v\d+\.\d+\.\d+", tag) else "skip"


def _minisign_signature(path: Path) -> str:
    """Publish only a signature the pinned updater can parse.

    The client base64-decodes the .sig file and hands the bytes to minisign,
    which reads four lines: the untrusted comment, the 74-byte signature, the
    trusted comment and the 64-byte global signature. A truncated or
    mismatched file must fail here, not after the client has downloaded the
    artifact.
    """
    encoded = path.read_text(encoding="utf-8").strip()
    if not encoded:
        raise ValueError(f"missing signature: {path.name}")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"invalid signature encoding: {path.name}") from error
    lines = decoded.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) != 4 or not lines[0].startswith(b"untrusted comment: "):
        raise ValueError(f"invalid signature contents: {path.name}")
    if not lines[2].startswith(b"trusted comment: "):
        raise ValueError(f"invalid signature contents: {path.name}")
    try:
        signature = base64.b64decode(lines[1], validate=True)
        global_signature = base64.b64decode(lines[3], validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"invalid signature contents: {path.name}") from error
    if len(signature) != 74 or len(global_signature) != 64:
        raise ValueError(f"invalid signature contents: {path.name}")
    return encoded


def _channel_version_key(channel: str, value: object) -> tuple[int, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("version"), str):
        raise ValueError("desktop update feed must contain a string version")
    if channel == "stable":
        pattern = STABLE_VERSION
    elif channel == "main":
        pattern = MAIN_VERSION
    else:
        raise ValueError("unsupported desktop update channel")
    match = pattern.fullmatch(value["version"])
    if match is None:
        raise ValueError(f"invalid {channel} desktop version")
    return tuple(int(part) for part in match.groups())


def channel_pointer_decision(channel: str, current: object, candidate: object) -> str:
    """Publish a mutable channel pointer only at a nondecreasing version."""
    current_key = _channel_version_key(channel, current)
    candidate_key = _channel_version_key(channel, candidate)
    return "advance" if candidate_key >= current_key else "retain"


def build(directory: Path, version: str, tag: str, channel: str) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", version):
        raise ValueError("invalid desktop version")
    if not re.fullmatch(r"[0-9A-Za-z._-]+", tag):
        raise ValueError("invalid release tag")
    if channel not in QUALIFIED_PLATFORMS:
        raise ValueError("unsupported desktop update channel")
    platforms = {}
    for platform, suffix in QUALIFIED_PLATFORMS[channel]:
        matches = list(directory.glob(f"*{suffix}"))
        if len(matches) != 1 or matches[0].stat().st_size == 0:
            raise ValueError(f"missing or ambiguous artifact: {platform}")
        artifact = matches[0]
        signature = _minisign_signature(artifact.with_name(artifact.name + ".sig"))
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
    parser.add_argument("--channel", choices=("main", "stable"))
    parser.add_argument("--channel-pointer-decision", choices=("stable", "main"))
    parser.add_argument("--current-feed", type=Path)
    parser.add_argument("--candidate-feed", type=Path)
    args = parser.parse_args()
    if args.classify_release is not None:
        print(release_channel(args.classify_release, args.prerelease == "true"))
        raise SystemExit(0)
    if args.channel_pointer_decision is not None:
        if args.current_feed is None or args.candidate_feed is None:
            parser.error(
                "--current-feed and --candidate-feed are required for a channel pointer decision"
            )
        current = json.loads(args.current_feed.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate_feed.read_text(encoding="utf-8"))
        print(
            channel_pointer_decision(args.channel_pointer_decision, current, candidate)
        )
        raise SystemExit(0)
    if args.directory is None or args.version is None or args.tag is None:
        parser.error("--directory, --version and --tag are required to build a feed")
    if args.channel is None:
        parser.error("--channel is required to build a feed")
    output = build(args.directory, args.version, args.tag, args.channel)
    (args.directory / "desktop-updater.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
