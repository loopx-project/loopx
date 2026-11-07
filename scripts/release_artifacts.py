#!/usr/bin/env python3
"""Validate LoopX release identity and produce canonical SHA-256 manifests."""

from __future__ import annotations

import argparse
import ast
import copy
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ReleaseArtifactError(ValueError):
    """Raised when release inputs do not satisfy the public release contract."""


@dataclass(frozen=True)
class ReleaseIdentity:
    name: str
    version: str

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def distribution_prefix(self) -> str:
        return self.name.replace("-", "_")


def _module_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    raise ReleaseArtifactError(f"missing literal __version__ in {path}")


def load_identity(project_root: Path) -> ReleaseIdentity:
    pyproject_path = project_root / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ReleaseArtifactError(f"missing [project] table in {pyproject_path}")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ReleaseArtifactError("project.name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ReleaseArtifactError("project.version must be a non-empty string")
    module_version = _module_version(project_root / "loopx" / "__init__.py")
    if module_version != version:
        raise ReleaseArtifactError(
            f"version mismatch: pyproject.toml={version!r}, loopx.__version__={module_version!r}"
        )
    return ReleaseIdentity(name=name, version=version)


def distribution_files(dist_dir: Path, identity: ReleaseIdentity) -> tuple[Path, ...]:
    if not dist_dir.is_dir():
        raise ReleaseArtifactError(f"distribution directory does not exist: {dist_dir}")
    prefix = f"{identity.distribution_prefix}-{identity.version}"
    wheel_files = sorted(dist_dir.glob(f"{prefix}-*.whl"))
    sdist_files = sorted(dist_dir.glob(f"{prefix}.tar.gz"))
    if len(wheel_files) != 1 or len(sdist_files) != 1:
        raise ReleaseArtifactError(
            "expected exactly one wheel and one sdist for "
            f"{identity.name} {identity.version}; found wheels={len(wheel_files)}, "
            f"sdists={len(sdist_files)}"
        )
    expected = tuple(sorted((*wheel_files, *sdist_files), key=lambda path: path.name))
    unexpected = sorted(
        path.name
        for path in dist_dir.iterdir()
        if path.is_file() and path not in expected
    )
    if unexpected:
        raise ReleaseArtifactError(f"unexpected files in distribution directory: {unexpected}")
    return expected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_manifest(files: tuple[Path, ...]) -> str:
    return "".join(f"{sha256(path)}  {path.name}\n" for path in files)


def _safe_archive_name(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def normalize_sdist(dist_dir: Path, identity: ReleaseIdentity, source_date_epoch: int) -> None:
    if source_date_epoch < 0:
        raise ReleaseArtifactError("source date epoch must be non-negative")
    files = distribution_files(dist_dir, identity)
    sdist = next(path for path in files if path.name.endswith(".tar.gz"))
    temporary = sdist.with_name(f".{sdist.name}.tmp")
    try:
        with tarfile.open(sdist, mode="r:gz") as source:
            members = sorted(source.getmembers(), key=lambda member: member.name)
            if any(not _safe_archive_name(member.name) for member in members):
                raise ReleaseArtifactError(f"sdist contains an unsafe archive path: {sdist}")
            payloads = {
                member.name: source.extractfile(member).read()
                for member in members
                if member.isfile()
            }
        with temporary.open("wb") as raw_stream:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_stream,
                mtime=source_date_epoch,
            ) as gzip_stream:
                with tarfile.open(
                    fileobj=gzip_stream,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as target:
                    for member in members:
                        normalized = copy.copy(member)
                        normalized.mtime = source_date_epoch
                        normalized.uid = 0
                        normalized.gid = 0
                        normalized.uname = ""
                        normalized.gname = ""
                        normalized.pax_headers = {
                            key: value
                            for key, value in member.pax_headers.items()
                            if key not in {"atime", "ctime", "gid", "gname", "mtime", "uid", "uname"}
                        }
                        data = io.BytesIO(payloads[member.name]) if member.isfile() else None
                        target.addfile(normalized, data)
        temporary.replace(sdist)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseArtifactError(f"cannot normalize sdist {sdist}: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"normalized {sdist} with SOURCE_DATE_EPOCH={source_date_epoch}")


def write_manifest(dist_dir: Path, output: Path, identity: ReleaseIdentity) -> None:
    files = distribution_files(dist_dir, identity)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(render_manifest(files), encoding="ascii")
    temporary.replace(output)
    print(f"wrote {output} for {len(files)} distributions")


def verify_manifest(dist_dir: Path, checksum_file: Path, identity: ReleaseIdentity) -> None:
    files = distribution_files(dist_dir, identity)
    expected = render_manifest(files)
    try:
        observed = checksum_file.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ReleaseArtifactError(f"cannot read checksum manifest {checksum_file}: {exc}") from exc
    if observed != expected:
        raise ReleaseArtifactError(
            f"checksum manifest does not match release distributions: {checksum_file}"
        )
    print(f"verified {checksum_file} for {len(files)} distributions")


def gh(*args: str) -> bytes:
    return subprocess.check_output(["gh", *args])


def verify_publication_run(repo: str, run_id: str, tag: str, commit: str) -> None:
    """Only reuse a successful tagged build from this repository's release workflow."""
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ReleaseArtifactError("validated run id must be a positive integer")
    run = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if (
        run["repository"]["full_name"] != repo
        or run["event"] != "release"
        or run["path"] != ".github/workflows/release-artifacts.yml"
        or run["head_sha"] != commit
        or run["head_branch"] != tag
    ):
        raise ReleaseArtifactError("recovery run does not match the tagged product build")
    jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"))
    builds = [job for job in jobs["jobs"] if job["name"] == "build"]
    if len(builds) != 1 or builds[0]["conclusion"] != "success":
        raise ReleaseArtifactError("recovery requires the original successful build job")
    print(f"verified successful build {run_id} for {tag}@{commit}")


def publication_files(dist_dir: Path, identity: ReleaseIdentity) -> tuple[Path, ...]:
    files = (*distribution_files(dist_dir / "packages", identity),
             dist_dir / "SHA256SUMS", dist_dir / "RELEASE-SOURCES.json",
             *sorted((dist_dir / "runtime").iterdir()))
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ReleaseArtifactError("publication inputs must be regular files")
    if len({path.name.casefold() for path in files}) != len(files):
        raise ReleaseArtifactError("publication names collide ignoring case")
    verify_manifest(dist_dir / "packages", dist_dir / "SHA256SUMS", identity)
    return tuple(files)


def upload_immutable(dist_dir: Path, identity: ReleaseIdentity, repo: str,
                     *, recover: bool = False, dry_run: bool = False) -> None:
    files = publication_files(dist_dir, identity)
    if recover:
        commit = gh("api", f"repos/{repo}/commits/{identity.tag}", "--jq", ".sha").decode().strip()
        sources = json.loads((dist_dir / "RELEASE-SOURCES.json").read_text())
        if sources["product_tag"] != identity.tag or sources["product_commit"] != commit:
            raise ReleaseArtifactError("bundle product identity differs from the release tag")
        for path in files:
            gh("attestation", "verify", str(path), "--repo", repo,
               "--signer-workflow", f"{repo}/.github/workflows/release-artifacts.yml",
               "--source-digest", commit, "--source-ref", f"refs/tags/{identity.tag}",
               "--deny-self-hosted-runners")
    release = json.loads(gh("api", f"repos/{repo}/releases/tags/{identity.tag}"))
    assets = json.loads(gh("api", "--paginate", "--slurp",
                          f"repos/{repo}/releases/{release['id']}/assets?per_page=100"))
    remote = {}
    for asset in (asset for page in assets for asset in page):
        key = asset["name"].casefold()
        if key in remote:
            raise ReleaseArtifactError("remote release names collide ignoring case")
        remote[key] = asset
    missing = []
    # Check every existing asset before the first upload; never delete or clobber.
    for path in files:
        asset = remote.get(path.name.casefold())
        if asset is None:
            missing.append(path)
            continue
        if asset["name"] != path.name:
            raise ReleaseArtifactError(f"release name collision: {asset['name']} / {path.name}")
        with tempfile.TemporaryFile() as downloaded:
            subprocess.run(["gh", "api", f"repos/{repo}/releases/assets/{asset['id']}",
                            "-H", "Accept: application/octet-stream"],
                           stdout=downloaded, check=True)
            downloaded.seek(0)
            actual = hashlib.file_digest(downloaded, "sha256").hexdigest()
        if asset["size"] != path.stat().st_size or actual != sha256(path):
            raise ReleaseArtifactError(f"immutable release asset differs: {path.name}")
    if missing and not dry_run:
        gh("release", "upload", identity.tag, *(str(path) for path in missing), "--repo", repo)
    print(f"verified {len(files) - len(missing)} existing assets; "
          f"{'would upload' if dry_run else 'uploaded'} {len(missing)} missing assets")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("expected-tag", help="print the tag required by package metadata")
    validate = subparsers.add_parser("validate-tag", help="validate a release tag")
    validate.add_argument("tag")

    normalize = subparsers.add_parser("normalize-sdist", help="normalize sdist metadata")
    normalize.add_argument("--dist-dir", type=Path, required=True)
    normalize.add_argument("--source-date-epoch", type=int, required=True)

    write = subparsers.add_parser("write-checksums", help="write a canonical SHA256SUMS file")
    write.add_argument("--dist-dir", type=Path, required=True)
    write.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify-checksums", help="verify a canonical SHA256SUMS file")
    verify.add_argument("--dist-dir", type=Path, required=True)
    verify.add_argument("--checksum-file", type=Path, required=True)

    recovery = subparsers.add_parser("verify-publication-run", help="verify a tagged build for publication recovery")
    recovery.add_argument("--repo", required=True)
    recovery.add_argument("--run-id", required=True)
    recovery.add_argument("--commit", required=True)
    upload = subparsers.add_parser("upload-immutable", help="resume publication without replacing existing bytes")
    upload.add_argument("--repo", required=True)
    upload.add_argument("--dist-dir", type=Path, required=True)
    upload.add_argument("--recover", action="store_true")
    upload.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        identity = load_identity(args.project_root.resolve())
        if args.command == "expected-tag":
            print(identity.tag)
        elif args.command == "validate-tag":
            if args.tag != identity.tag:
                raise ReleaseArtifactError(
                    f"release tag {args.tag!r} does not match package tag {identity.tag!r}"
                )
            print(f"validated release tag {args.tag}")
        elif args.command == "normalize-sdist":
            normalize_sdist(args.dist_dir, identity, args.source_date_epoch)
        elif args.command == "write-checksums":
            write_manifest(args.dist_dir, args.output, identity)
        elif args.command == "verify-checksums":
            verify_manifest(args.dist_dir, args.checksum_file, identity)
        elif args.command == "verify-publication-run":
            verify_publication_run(args.repo, args.run_id, identity.tag, args.commit)
        elif args.command == "upload-immutable":
            upload_immutable(args.dist_dir, identity, args.repo,
                             recover=args.recover, dry_run=args.dry_run)
        else:
            parser.error(f"unsupported command: {args.command}")
    except ReleaseArtifactError as exc:
        print(f"release artifact error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
