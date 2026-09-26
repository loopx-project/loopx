#!/usr/bin/env python3
"""Smoke-test tracked dashboard fixtures stay public-safe."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED_PATHS = [
    "apps/presentation/dashboard",
    "examples/status.example.json",
    "examples/render-status-dashboard.py",
]
TRACKED_EXAMPLE_PREFIXES = (
    "examples/dashboard-",
)
PRIVATE_SHAPES = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\]+\\\\"),
    re.compile(r"/private/var/folders/"),
    re.compile(r"code\.byted\.org"),
    re.compile("bnpm" + r"\.byted\.org"),
    re.compile(r"bytedance" + r"\." + "lark" + "office" + r"\.com"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+"),
]
IMAGE_SIGNATURES = {
    ".gif": (b"GIF87a", b"GIF89a"),
    ".jpeg": (b"\xff\xd8\xff",),
    ".jpg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


def tracked_dashboard_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", *TRACKED_PATHS, *TRACKED_EXAMPLE_PREFIXES],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = Path(line)
        if path.parts[0] == "apps" or path.name.startswith("dashboard-"):
            paths.append(path)
        elif path.name in {"status.example.json", "render-status-dashboard.py"}:
            paths.append(path)
    return sorted(set(paths))


def assert_public_safe(text: str, *, label: str) -> None:
    for pattern in PRIVATE_SHAPES:
        if pattern.search(text):
            raise AssertionError(f"{label} matched private shape {pattern.pattern!r}")


def assert_tracked_file_public_safe(path: Path) -> None:
    payload = path.read_bytes()
    signatures = IMAGE_SIGNATURES.get(path.suffix.lower())
    if signatures is not None:
        if not any(payload.startswith(signature) for signature in signatures):
            raise AssertionError(f"{path} does not match its image signature")
        if path.suffix.lower() == ".webp" and payload[8:12] != b"WEBP":
            raise AssertionError(f"{path} does not match its WebP signature")
        return

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"unrecognized binary tracked fixture: {path}") from exc
    assert_public_safe(text, label=str(path))


def main() -> int:
    files = tracked_dashboard_files()
    if not files:
        raise AssertionError("no tracked dashboard files found")

    for path in files:
        assert_tracked_file_public_safe(REPO_ROOT / path)

    assert_public_safe("./fixtures/runtime", label="relative fixture path")
    assert_public_safe("/tmp/loopx-dashboard-smoke", label="temporary path")

    for private_text in [
        "/Users/alice/.loopx/registry.global.json",
        "/home/alice/.loopx/registry.global.json",
        r"C:\\Users\\alice\\.codex\\loopx\\registry.global.json",
        "https://code.byted.org/private/project",
        "Bearer " + "abcdef123456",
    ]:
        try:
            assert_public_safe(private_text, label="negative fixture")
        except AssertionError:
            pass
        else:
            raise AssertionError(f"private fixture was not rejected: {private_text}")

    print(f"dashboard-tracked-fixtures-smoke ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
