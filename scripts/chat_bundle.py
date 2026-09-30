#!/usr/bin/env python3
"""Build, verify and stage Chat assets; generated output never belongs in Git."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# The contract asks the shared digest owner what a stored digest looks like, so loading
# it by file path needs the checkout importable: a source build has no installed LoopX.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "chat_bundle_contract", ROOT / "loopx/presentation/chat_bundle.py"
)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
OUTPUT = ROOT / "loopx/web/chat"


def previous_assets(previous: Path | None) -> list[str]:
    if previous is None:
        return []
    if (previous / contract.MANIFEST).is_file():
        return contract.validate_bundle(previous)["current_assets"]
    # Legacy release wheels predate provenance metadata. Include their bounded
    # asset directory for this one transition; never load history from Git.
    if not (previous / "index.html").is_file():
        raise RuntimeError("previous delivery has no index.html")
    assets = sorted(
        p.relative_to(previous).as_posix()
        for p in (previous / "assets").rglob("*")
        if p.is_file()
    )
    entries = re.findall(
        r'(?:src|href)="/chat/(assets/[^\"]+)"',
        (previous / "index.html").read_text(encoding="utf-8"),
    )
    if not entries or not set(entries) <= set(assets):
        raise RuntimeError("previous delivery has missing entry assets")
    return assets


def build(previous: Path | None, *, install: bool = False) -> None:
    dashboard = ROOT / "apps/presentation/dashboard"
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError(
            "Node.js/npm is required to build a source checkout; release wheels already contain the frontend"
        )
    if install:
        subprocess.run([npm, "ci", "--ignore-scripts"], cwd=dashboard, check=True)
    before = contract.source_inputs(ROOT)
    previous_assets(previous)  # Reject invalid lineage before spending a build.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".chat-build-", dir=OUTPUT.parent
    ) as temporary:
        staged = Path(temporary) / "chat"
        subprocess.run(
            [npm, "run", "build:chat:vite"],
            cwd=dashboard,
            env={**os.environ, "LOOPX_CHAT_OUT_DIR": str(staged)},
            check=True,
        )
        current = sorted(
            p.relative_to(staged).as_posix()
            for p in (staged / "assets").rglob("*")
            if p.is_file()
        )
        if before != contract.source_inputs(ROOT):
            raise RuntimeError(
                "source changed while building; retry without concurrent edits"
            )
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        source_revision = (
            revision.stdout.strip()
            if revision.returncode == 0
            else os.environ.get("LOOPX_RESOLVED_SOURCE_GIT_COMMIT")
        )
        finish_delivery(staged, previous, current, before, source_revision)
    print(
        "Chat bundle built and verified; current and previous delivery assets retained."
    )


def finish_delivery(
    staged: Path,
    previous: Path | None,
    current: list[str],
    inputs: dict,
    revision: str | None,
) -> None:
    old_assets = previous_assets(previous)
    for name in old_assets:
        source = previous / name
        if (
            not contract.safe_relative(name)
            or not name.startswith("assets/")
            or source.is_symlink()
            or not source.resolve().is_relative_to(previous.resolve())
        ):
            raise RuntimeError("invalid previous asset")
        target = staged / name
        if target.exists() and contract.digest(source) != contract.digest(target):
            raise RuntimeError(
                "same asset name has different content across deliveries"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    generations = [current]
    if set(old_assets) - set(current):
        generations.append(old_assets)
    (staged / "asset-retention.json").write_text(
        json.dumps(
            {
                "schema_version": "loopx_chat_asset_retention_v1",
                "generations": generations,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": contract.CHAT_BUNDLE_SCHEMA_VERSION,
        "source_revision": revision,
        "source_files": inputs,
        "current_assets": current,
        "files": {
            p.relative_to(staged).as_posix(): contract.digest(p)
            for p in sorted(staged.rglob("*"))
            if p.is_file() and p.name != contract.MANIFEST
        },
    }
    (staged / contract.MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    contract.validate_bundle(staged, source_root=ROOT)
    backup = staged.parent / "previous"
    if OUTPUT.exists():
        OUTPUT.rename(backup)
    try:
        staged.rename(OUTPUT)
    except OSError:
        if backup.exists():
            backup.rename(OUTPUT)
        raise


def ensure(previous: Path | None = None) -> None:
    try:
        manifest = contract.validate_bundle(OUTPUT, source_root=ROOT)
    except RuntimeError:
        build(previous, install=True)
        return
    if previous is None or previous.resolve() == OUTPUT.resolve():
        return
    # A bundled desktop/archive install must not need Node or network merely to
    # carry forward the currently installed delivery's assets.
    with tempfile.TemporaryDirectory(
        prefix=".chat-build-", dir=OUTPUT.parent
    ) as temporary:
        staged = Path(temporary) / "chat"
        for name in manifest["files"]:
            if name.startswith("assets/") and name not in manifest["current_assets"]:
                continue
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(OUTPUT / name, target)
        finish_delivery(
            staged,
            previous,
            manifest["current_assets"],
            manifest["source_files"],
            manifest["source_revision"],
        )


def release_build(tag: str, repository: str) -> None:
    # Use the preceding published version, never the latest desktop prerelease
    # or a future release when rebuilding an older tag.
    pages = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/releases?per_page=100",
            ],
            text=True,
        )
    )
    versions = [
        r
        for page in pages
        for r in page
        if not r["draft"]
        and not r["prerelease"]
        and re.fullmatch(r"v\d+\.\d+\.\d+", r["tag_name"])
    ]

    def number(value: str) -> tuple[int, ...]:
        return tuple(map(int, value.removeprefix("v").split(".")))

    predecessors = [r for r in versions if number(r["tag_name"]) < number(tag)]
    if not predecessors:
        build(None, install=True)
        return
    previous = max(predecessors, key=lambda r: number(r["tag_name"]))
    wheels = [
        a["name"]
        for a in previous["assets"]
        if a["name"].startswith("loopx-") and a["name"].endswith(".whl")
    ]
    if len(wheels) != 1:
        raise RuntimeError("preceding release must contain exactly one LoopX wheel")
    with tempfile.TemporaryDirectory(prefix="loopx-previous-release-") as temporary:
        directory = Path(temporary)
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                previous["tag_name"],
                "--repo",
                repository,
                "--pattern",
                wheels[0],
                "--pattern",
                "SHA256SUMS",
                "--dir",
                str(directory),
            ],
            check=True,
        )
        checksums = (directory / "SHA256SUMS").read_text().splitlines()
        matches = [
            line.split()[0]
            for line in checksums
            if len(line.split()) == 2
            and Path(line.split()[1].lstrip("*")).name == wheels[0]
        ]
        if matches != [contract.digest(directory / wheels[0])]:
            raise RuntimeError("previous release wheel checksum mismatch")
        old_bundle = directory / "chat"
        extract_wheel(directory / wheels[0], old_bundle)
        build(old_bundle, install=True)


def extract_wheel(wheel: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError("previous bundle destination must not exist")
    with zipfile.ZipFile(wheel) as archive:
        prefix = "loopx/web/chat/"
        entries = [
            item
            for item in archive.infolist()
            if item.filename.startswith(prefix) and not item.is_dir()
        ]
        if len({item.filename for item in entries}) != len(entries):
            raise RuntimeError("duplicate previous wheel entries")
        if not entries or sum(item.file_size for item in entries) > 128 * 1024 * 1024:
            raise RuntimeError("previous wheel has no bounded Chat bundle")
        for item in entries:
            name = item.filename[len(prefix) :]
            if (
                not contract.safe_relative(name)
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise RuntimeError("invalid previous wheel path")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
    previous_assets(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build")
    b.add_argument("--previous", type=Path)
    b.add_argument("--install", action="store_true")
    a = commands.add_parser("ensure")
    a.add_argument("--previous", type=Path)
    v = commands.add_parser("verify")
    v.add_argument("--bundle", type=Path, default=OUTPUT)
    v.add_argument("--source", action="store_true")
    e = commands.add_parser("extract-wheel")
    e.add_argument("wheel", type=Path)
    e.add_argument("destination", type=Path)
    r = commands.add_parser("release-build")
    r.add_argument("--tag", required=True)
    r.add_argument("--repo", required=True)
    args = parser.parse_args()
    if args.command == "build":
        build(args.previous, install=args.install)
    elif args.command == "ensure":
        ensure(args.previous)
    elif args.command == "verify":
        contract.validate_bundle(args.bundle, source_root=ROOT if args.source else None)
        print("Chat bundle verified")
    elif args.command == "release-build":
        release_build(args.tag, args.repo)
    else:
        extract_wheel(args.wheel, args.destination)


if __name__ == "__main__":
    main()
