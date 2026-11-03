#!/usr/bin/env python3
"""Build audited runtime + optional resources from one committed source revision.

Keep runtime code, all skills/references, text documentation, developer smokes,
and the exact CI-bound frontend bundle contract. Frontend development sources
and named presentation media are optional; docs/examples text stays available.
The archive is installed by the existing snapshot installer, without npm.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loopx.presentation.chat_bundle import source_digest, validate_bundle
from loopx.cli_commands.update_artifact import RUNTIME_ARTIFACT_SCHEMA_VERSION, validate_manifest

PATHS = ("loopx", "scripts", "skills", "docs", "man", "examples", "apps/presentation",
         ".github", "README.md", "LICENSE", "NOTICE", "LICENSE-MIT", "pyproject.toml", "setup.py", "MANIFEST.in")
# Static product illustrations/browser captures are not used by CLI execution,
# skills, installer validation, TS runtime or Chat's source/bundle validator.
# Text/HTML/SVG and any exact frontend input remain in the runtime below.
OPTIONAL_MEDIA_ROOTS = (
    "docs/assets/", "docs/development/assets/",
    "docs/architecture/rfcs/", "docs/reference/assets/", "docs/reference/media/",
    "examples/personal-workspace-browser/evidence/",
    "examples/personal-workspace-browser/assets/",
    "examples/personal-workspace-browser/screenshots/",
    "examples/collaboration-delivery/screenshots/",
)
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm"}


def build(root: Path, output: Path, *, repository: str, channel: str) -> dict:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = validate_bundle(root / "loopx/web/chat", source_root=root)
    if manifest["source_revision"] != revision:
        raise ValueError("Frontend must belong to the exact committed source revision")
    for name, digest in manifest["source_files"].items():
        committed = subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=root)
        if source_digest(Path(name), committed) != digest:
            raise ValueError(f"Frontend input differs from committed source: {name}")
    output.mkdir(parents=True, exist_ok=True)
    filename = f"loopx-runtime-{revision}.tar.gz"
    optional_name = f"loopx-resources-{revision}.tar.gz"
    inventory: dict[str, list] = {"runtime": [], "optional_resources": []}
    version = None
    # Stream Git, never enumerate the working directory (private files stay out).
    git = subprocess.Popen(["git", "archive", revision, *PATHS], cwd=root, stdout=subprocess.PIPE)
    with (
        (output / filename).open("wb") as runtime_file,
        (output / optional_name).open("wb") as resources_file,
        gzip.GzipFile(fileobj=runtime_file, mode="wb", mtime=0, filename="") as runtime_gzip,
        gzip.GzipFile(fileobj=resources_file, mode="wb", mtime=0, filename="") as resources_gzip,
        tarfile.open(fileobj=runtime_gzip, mode="w|", format=tarfile.PAX_FORMAT) as runtime,
        tarfile.open(fileobj=resources_gzip, mode="w|", format=tarfile.PAX_FORMAT) as resources,
        tarfile.open(fileobj=git.stdout, mode="r|") as source,
    ):
        for member in source:
            if not member.isfile():
                if not member.isdir():
                    raise ValueError(f"Non-file runtime entry: {member.name}")
                continue
            if member.name.startswith("loopx/web/chat/"):
                continue
            content = source.extractfile(member).read()
            if member.name == "pyproject.toml":
                version = tomllib.loads(content.decode())["project"]["version"]
            optional = member.name.startswith("apps/presentation/") or (member.name.startswith(OPTIONAL_MEDIA_ROOTS)
                and Path(member.name).suffix.lower() in MEDIA_SUFFIXES
                and member.name not in manifest["source_files"])
            sink = resources if optional else runtime
            group = "optional_resources" if optional else "runtime"
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            sink.addfile(member, io.BytesIO(content))
            inventory[group].append({"path": member.name, "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()})
        for name in sorted({*manifest["files"], "bundle-manifest.json"}):
            content = (root / "loopx/web/chat" / name).read_bytes()
            member = tarfile.TarInfo("loopx/web/chat/" + name)
            member.size, member.mode = len(content), 0o644
            runtime.addfile(member, io.BytesIO(content))
            inventory["runtime"].append({"path": member.name, "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()})
        identity = json.dumps({"schema_version": RUNTIME_ARTIFACT_SCHEMA_VERSION, "source_revision": revision,
            "package_version": version}).encode()
        member = tarfile.TarInfo("runtime-artifact.json")
        member.size, member.mode = len(identity), 0o644
        runtime.addfile(member, io.BytesIO(identity))
        inventory["runtime"].append({"path": member.name, "bytes": len(identity),
            "sha256": hashlib.sha256(identity).hexdigest()})
    if git.wait() != 0 or not version:
        raise ValueError("Git runtime archive failed")
    tag = "runtime-main" if channel == "main" else "v" + version
    archive = output / filename
    result = validate_manifest({"schema_version": RUNTIME_ARTIFACT_SCHEMA_VERSION, "source_revision": revision,
        "package_version": version, "channel": channel, "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "urls": [f"https://github.com/{repository}/releases/download/{tag}/{filename}"],
        "resources": {"filename": optional_name, "size": (output / optional_name).stat().st_size,
            "sha256": hashlib.sha256((output / optional_name).read_bytes()).hexdigest()}})
    (output / "loopx-runtime.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "runtime-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(json.dumps({"revision": revision, "runtime_bytes": result["size"],
        "optional_bytes": result["resources"]["size"],
        "runtime_files": len(inventory["runtime"]), "optional_files": len(inventory["optional_resources"])}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/runtime")
    parser.add_argument("--repository", default="loopx-project/loopx")
    parser.add_argument("--channel", choices=("main", "stable"), required=True)
    args = parser.parse_args()
    build(ROOT, args.output, repository=args.repository, channel=args.channel)
