"""Integrity and source freshness for the generated, packaged Chat frontend."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
# Absolute by necessity: setup.py and the two build scripts exec this module by file
# path, so there is no package context for a relative import. Each of those loaders puts
# the checkout root on sys.path before exec'ing it, which is what keeps a source build
# working when LoopX is not installed.
from loopx.control_plane.content_digest import BARE_SHA256_PATTERN

MANIFEST = "bundle-manifest.json"
CHAT_BUNDLE_SCHEMA_VERSION = "loopx_chat_bundle_v1"
SOURCE_ROOTS = (
    "apps/presentation/dashboard/src",
    "apps/presentation/dashboard/chat",
    "apps/presentation/dashboard/public",
)
SOURCE_FILES = (
    "apps/presentation/dashboard/package.json",
    "apps/presentation/dashboard/package-lock.json",
    "apps/presentation/dashboard/vite.chat.config.ts",
    "apps/presentation/dashboard/tsconfig.json",
)
BUILD_HELP = "Run npm ci and npm run build:chat in apps/presentation/dashboard (or reinstall a complete LoopX package)."


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(path: Path, content: bytes | None = None) -> str:
    # Git may check text out with CRLF on Windows; binary assets remain exact.
    data = path.read_bytes() if content is None else content
    if path.name == ".gitkeep" or path.suffix in {
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".json",
        ".html",
        ".css",
        ".svg",
        ".webmanifest",
        ".py",
    }:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def source_inputs(root: Path) -> dict[str, str]:
    paths = [root / name for name in SOURCE_FILES]
    for name in SOURCE_ROOTS:
        paths.extend(
            path
            for path in (root / name).rglob("*")
            if path.is_file() and path.name != ".gitkeep"
            and not path.name.endswith(".local.json")
        )
    # Shared typed contracts imported by the frontend are build inputs too.
    paths.extend((root / "loopx/control_plane").rglob("*.ts"))
    paths.extend((root / "loopx/control_plane").rglob("*.json"))
    paths.append(root / "scripts/chat_bundle.py")
    paths.append(root / "loopx/presentation/chat_bundle.py")
    return {
        path.relative_to(root).as_posix(): source_digest(path)
        for path in sorted(paths)
        if path.is_file()
    }


def safe_relative(value: str) -> bool:
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and ":" not in value
        and "\0" not in value
        and str(path) == value
    )


def validate_bundle(bundle: Path, *, source_root: Path | None = None) -> dict:
    try:
        manifest = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != CHAT_BUNDLE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported bundle manifest")
        files = manifest["files"]
        if (
            not isinstance(files, dict)
            or not {"index.html", "manifest.webmanifest", "asset-retention.json"}
            <= files.keys()
        ):
            raise ValueError("incomplete bundle")
        actual = {
            p.relative_to(bundle).as_posix()
            for p in bundle.rglob("*")
            if p.is_file() and p.relative_to(bundle).as_posix() != MANIFEST
        }
        if actual != files.keys():
            raise ValueError("bundle file inventory changed")
        for name, expected in files.items():
            if (
                not safe_relative(name)
                or not isinstance(expected, str)
                or not BARE_SHA256_PATTERN.fullmatch(expected)
            ):
                raise ValueError("invalid bundle witness")
            path = bundle / name
            if (
                path.is_symlink()
                or any(
                    (bundle / Path(*PurePosixPath(name).parts[:i])).is_symlink()
                    for i in range(1, len(PurePosixPath(name).parts) + 1)
                )
                or digest(path) != expected
            ):
                raise ValueError(f"bundle file changed: {name}")
        current = manifest["current_assets"]
        retention = json.loads(
            (bundle / "asset-retention.json").read_text(encoding="utf-8")
        )
        generations = retention["generations"]
        if not isinstance(generations, list) or any(
            not isinstance(g, list)
            or not g
            or any(
                not isinstance(n, str)
                or not safe_relative(n)
                or not n.startswith("assets/")
                for n in g
            )
            or len(g) != len(set(g))
            for g in generations
        ):
            raise ValueError("invalid asset generation entries")
        if (
            retention.get("schema_version") != "loopx_chat_asset_retention_v1"
            or not 1 <= len(generations) <= 2
            or generations[0] != current
        ):
            raise ValueError("invalid asset generations")
        if set().union(*(set(g) for g in generations)) != {
            name for name in files if name.startswith("assets/")
        }:
            raise ValueError("asset retention is incomplete")
        entries = re.findall(
            r'(?:src|href)="/chat/(assets/[^\"]+)"',
            (bundle / "index.html").read_text(encoding="utf-8"),
        )
        if not entries or not set(entries) <= set(current):
            raise ValueError("entry references missing assets")
        if (
            source_root is not None
            and (source_root / "apps/presentation/dashboard/package.json").is_file()
        ):
            if manifest.get("source_files") != source_inputs(source_root):
                raise ValueError("frontend source changed since the last build")
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError(
            f"LoopX Chat bundle is missing, stale or invalid: {error}. {BUILD_HELP}"
        ) from error
