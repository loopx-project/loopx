"""Stage a canonical product archive without promoting an installed profile.

The caller first creates it with benchmark.runtime.source.archive_source from a
qualified clean revision. No installer, hook, skill, credential or clock runs in
the active worker. The existing launcher is changed only by the barrier owner.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import tarfile
import tempfile

from .cli_source_activation import _validate_plan
from .cli_source_probe import _archive_response, _path_stat


def _inventory(archive, prefix=""):
    result = {}
    for entry in archive:
        path = PurePosixPath(entry.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Versioned release archive contains an invalid path")
        if prefix:
            if not path.parts or path.parts[0] != prefix:
                raise ValueError("Versioned release archive differs from its root")
            path = PurePosixPath(*path.parts[1:])
            if str(path) == ".":
                if not entry.isdir():
                    raise ValueError("Versioned release root must be physical")
                continue
        name = str(path)
        if name in result or name == "." or not (entry.isfile() or entry.isdir() or entry.issym()):
            raise ValueError("Versioned release archive contains unsupported members")
        item = {"mode": entry.mode & 0o777, "kind": entry.type}
        if entry.isfile():
            item["sha256"] = hashlib.file_digest(archive.extractfile(entry), "sha256").hexdigest()
        if entry.issym():
            # Git's archive owns the exact link bytes. Verification never
            # follows them or interprets source material as a command.
            item["target"] = entry.linkname
            target = posixpath.normpath(posixpath.join(str(path.parent), entry.linkname))
            if target.startswith("../") or target == ".." or target.startswith("/"):
                raise ValueError("Versioned release link leaves its source root")
        result[name] = item
    if any(result.get(str(parent), {}).get("kind") == tarfile.SYMTYPE
           for name in result for parent in PurePosixPath(name).parents):
        raise ValueError("Versioned release member traverses a symbolic parent")
    return result


def _physical_directory(container, path):
    for ancestor in reversed((PurePosixPath(path), *PurePosixPath(path).parents)):
        stat = _path_stat(container, str(ancestor))
        if stat.get("linkTarget") or not stat["mode"] & (1 << 31):
            raise ValueError("Versioned release ancestors must be physical directories")


def _matches(expected, actual):
    # archive_source appends validated generated assets as files. Tar extraction
    # creates any omitted physical parent directories; those are not extra
    # executable/source members and have no archive-declared permission mode.
    parents = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    if any(actual.get(name) != item for name, item in expected.items()):
        return False
    return all(name in parents and item["kind"] == tarfile.DIRTYPE
               for name, item in actual.items() if name not in expected)


class StagedCliRelease:
    """Exact canonical archive verifier used before and inside the pause."""
    def __init__(self, archive: Path, plan: dict):
        _validate_plan(plan)
        if archive.resolve(strict=True) != archive or not archive.is_file():
            raise ValueError("CLI source archive must be physical")
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != plan["target_archive_sha256"]:
                raise ValueError("CLI source archive digest differs")
        with tarfile.open(archive) as source:
            self.inventory = _inventory(source)
            if source.pax_headers.get("comment") != plan["target_revision"]:
                raise ValueError("CLI source archive does not bind its Git revision")
        launcher = self.inventory.get("scripts/loopx", {})
        if launcher.get("kind") != tarfile.REGTYPE or launcher.get("mode", 0) & 0o111 == 0:
            raise ValueError("CLI source archive has no executable launcher")
        self.archive, self.plan = archive, plan.copy()
        self.root = str(PurePosixPath(plan["new_target"]).parent.parent)

    def verify(self, container, plan):
        if plan != self.plan:
            raise ValueError("CLI release verification differs from its declared source")
        _physical_directory(container, self.root)
        with _archive_response(container, self.root) as (chunks, _), tempfile.TemporaryFile() as spool:
            for block in chunks:
                spool.write(block)
            spool.seek(0)
            with tarfile.open(fileobj=spool) as archive:
                actual = _inventory(archive, PurePosixPath(self.root).name)
        if not _matches(self.inventory, actual):
            raise ValueError("Staged CLI release bytes, modes or members differ")

    def stage(self, container, *, execute=False):
        """Explicit preparation only; never switches an existing launcher."""
        if type(execute) is not bool:
            raise ValueError("CLI release staging requires explicit execute")
        if not execute:
            return {"staged": False, "launcher_changed": False, "execution_authority_granted": False}
        with tempfile.TemporaryFile() as snapshot:
            with self.archive.open("rb") as stream:
                shutil.copyfileobj(stream, snapshot, 2 * 1024 * 1024)
            snapshot.seek(0)
            if hashlib.file_digest(snapshot, "sha256").hexdigest() != self.plan["target_archive_sha256"]:
                raise ValueError("CLI source archive changed after qualification")
            snapshot.seek(0)
            _physical_directory(container, self.plan["release_root"])
            # mkdir (without -p) rejects a prior/partial release; never overwrite
            # a directory or turn an uncertain upload into success.
            if container.exec_run(["mkdir", "--", self.root], user="root").exit_code:
                raise ValueError("CLI release destination already exists or is unavailable")
            if not container.put_archive(self.root, snapshot):
                raise RuntimeError("CLI source staging transport was not confirmed")
        self.verify(container, self.plan)
        return {"staged": True, "launcher_changed": False, "execution_authority_granted": False}
