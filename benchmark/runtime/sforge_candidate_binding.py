"""Read native archive facts, with an optional legacy local-probe diagnostic."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zlib

from benchmark.runtime.research_probe import candidate_manifest_sha256

_MANIFEST_LIMIT = 64 * 1024
_RESULT_LIMIT = 8 * 1024 * 1024
_ARCHIVE_LIMIT = 64 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _read(path: Path, limit: int) -> bytes:
    if not path.is_file():
        raise ValueError("candidate binding input must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("candidate binding input exceeds the supported size")
    return raw


def _object(path: Path, limit: int = _MANIFEST_LIMIT) -> dict:
    value = json.loads(_read(path, limit))
    if not isinstance(value, dict):
        raise ValueError("candidate binding input must be a JSON object")
    return value


def _safe_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError("invalid candidate member path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("candidate member path must stay within the snapshot")
    return path


def _file_hashes(rows: list[dict]) -> dict[str, str]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("select one to 32 candidate files")
    files = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("candidate file entry must be a JSON object")
        name, digest = row.get("path"), row.get("sha256")
        path = _safe_path(name)
        if (not path.parts or name != path.as_posix() or name in files
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError("candidate file paths and hashes must be canonical and distinct")
        files[name] = digest
    return files


def _file_set_digest(files: dict[str, str]) -> str:
    return candidate_manifest_sha256(
        [{"path": name, "sha256": digest} for name, digest in sorted(files.items())]
    )


def _archive_file_hashes(raw: bytes) -> dict[str, str]:
    # Bound decompression before parsing PAX or other tar headers. No member
    # is extracted to the filesystem.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        payload = compressed.read(_ARCHIVE_LIMIT + 1)
    if len(payload) > _ARCHIVE_LIMIT:
        raise ValueError("expanded native archive exceeds the supported size")
    files = {}
    seen = set()
    directories = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            path = _safe_path(member.name)
            if len(seen) >= 256 or path in seen:
                raise ValueError("native archive has too many or duplicate members")
            seen.add(path)
            if member.isdir():
                directories.add(path)
                continue
            if (not path.parts or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or member.issparse()):
                raise ValueError("native archive must contain ordinary files and their directories")
            with archive.extractfile(member) as stream:
                files[path.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        # A standard tar has two terminal zero blocks followed by zero padding.
        # Reject a truncated archive or a second concatenated archive.
        if len(payload) - archive.offset < 1024 or any(memoryview(payload)[archive.offset:]):
            raise ValueError("native archive has an invalid tar terminator")
    parents = {parent for name in files for parent in PurePosixPath(name).parents}
    if not directories <= parents or any(parent.as_posix() in files for parent in parents):
        raise ValueError("native archive directories must be unambiguous ancestors of files")
    return files


def verify_binding(observation: Path, snapshot: Path, result: Path, archive: Path,
                   generated_files: list[tuple[str, Path]]) -> dict:
    probe = _object(observation)
    pins = _object(observation.with_suffix(observation.suffix + ".candidate.json"))
    pin_hashes = _file_hashes(pins.get("files"))
    probe_digest = _file_set_digest(pin_hashes)
    revision = probe.get("input_revision")
    evidence, stop = probe.get("evidence_refs"), probe.get("stop_reason")
    if (probe.get("schema_version") != "explore_execution_observation_v0"
            or type(probe.get("exit_code")) is not int or probe["exit_code"] != 0
            or not isinstance(stop, str) or stop.split(";", 1)[0] != "process_exit:0"
            or not isinstance(evidence, list)
            or not isinstance(revision, str) or not revision
            or probe.get("candidate_sha256") != probe_digest
            or pins.get("candidate_sha256") != probe_digest
            or "source:" + probe_digest not in evidence):
        raise ValueError("a completed probe with matching pinned source evidence is required")

    manifest = _object(snapshot)
    linked = manifest.get("execution_observation")
    if (not isinstance(linked, str) or not linked or Path(linked).is_absolute()
            or (snapshot.parent / linked).resolve() != observation.resolve()
            or manifest.get("input_revision") != revision):
        raise ValueError("the submitted snapshot must cite this probe and its input revision")
    expected = _file_hashes(manifest.get("files"))
    shared = expected.keys() & pin_hashes.keys()
    generated = dict(generated_files)
    if (not shared or any(expected[name] != pin_hashes[name] for name in shared)
            or len(generated) != len(generated_files) or generated.keys() != expected.keys() - shared):
        raise ValueError("each submitted file must be pinned by the probe or an explicit generated output")
    for name, path in generated.items():
        if hashlib.sha256(_read(path, _ARCHIVE_LIMIT)).hexdigest() != expected[name]:
            raise ValueError("generated output bytes differ from the sealed snapshot")

    binding = native_archive_references(result, archive)
    if binding["files"] != expected:
        raise ValueError("native archive file paths or contents differ from the sealed snapshot")
    return {
        "ok": True,
        "candidate_ref": binding["archive_ref"],
        "submitted_file_set_ref": binding["submitted_file_set_ref"],
        "probe_candidate_ref": "candidate:" + probe_digest,
        "input_revision": revision,
        "submission_id": binding["submission_id"],
        "task_id": binding["task_id"],
        "round_id": binding["round_id"],
    }


def native_archive_references(result: Path | dict, archive: Path | None = None) -> dict:
    """Check the original receipt and uploaded bytes without a local execution.

    Archive identity preserves packaging/mode. The separate file-content ref
    permits a producer to name that subject; it does not establish execution
    equivalence, authenticate an offline result, or relabel earlier evidence.
    """
    native = result if isinstance(result, dict) else _object(result, _RESULT_LIMIT)
    receipt = native.get("receipt")
    if (native.get("status") != "completed" or not isinstance(receipt, dict)
            or receipt.get("schema_version") != "sforge_submission_receipt_v1"
            or any(not isinstance(value, str) or not value for value in (
                native.get("submission_id"), receipt.get("task_id"), receipt.get("round_id")))
            or not receipt["round_id"].startswith("agent-")):
        raise ValueError("a completed native agent result with its source receipt is required")
    digest, size = receipt.get("archive_sha256"), receipt.get("archive_bytes")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None or type(size) is not int or size <= 0:
        raise ValueError("a native source receipt with its archive digest and size is required")
    references = {
        "archive_ref": "archive:" + digest,
        "archive_bytes": size,
        "submission_id": native["submission_id"],
        "task_id": receipt["task_id"],
        "round_id": receipt["round_id"],
    }
    if archive is not None:
        raw = _read(archive, _ARCHIVE_LIMIT)
        if hashlib.sha256(raw).hexdigest() != digest or size != len(raw):
            raise ValueError("archive bytes do not match the native source receipt")
        files = _archive_file_hashes(raw)
        references.update(submitted_file_set_ref="candidate:" + _file_set_digest(files), files=files)
    return references


def _generated_file(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not name or not sep or not path:
        raise argparse.ArgumentTypeError("use SUBMITTED_PATH=PROBE_OUTPUT_PATH")
    return name, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Optional retained upload for file-content evidence")
    parser.add_argument("--observation", type=Path, help="Legacy offline local-probe diagnostic")
    parser.add_argument("--snapshot", type=Path, help="Legacy offline sealed probe manifest")
    result_source = parser.add_mutually_exclusive_group(required=True)
    result_source.add_argument("--result", type=Path, help="Retained native result JSON for offline verification")
    result_source.add_argument("--submission-id", help="Read one existing result from the installed native relay")
    parser.add_argument("--generated-file", dest="generated_files", type=_generated_file,
                        action="append", default=[], metavar="SUBMITTED_PATH=PROBE_OUTPUT_PATH")
    args = parser.parse_args(argv)
    if args.submission_id is not None:
        from http.client import HTTPException

        from benchmark.runtime.sforge_native_result import formal_measurement, read_installed_scope

        try:
            result = formal_measurement(read_installed_scope(), args.submission_id, archive=args.archive)
        except (OSError, ValueError, EOFError, HTTPException):
            print("Scoped native result unavailable; use sforge-submit --result ID --json for ordinary feedback.",
                  file=sys.stderr)
            return 1
        print(json.dumps(result))
        native = result["native"]
        status = native.get("status")
        return 0 if result["native_http_status"] == 200 and (
            status in ("queued", "running")
            or (status == "completed" and isinstance(native.get("report"), dict))
        ) else 1
    if ((args.observation is None) != (args.snapshot is None)
            or args.observation is not None and args.archive is None
            or args.generated_files and args.observation is None):
        parser.error("Legacy probe diagnostics require --observation, --snapshot and --archive")
    try:
        result = (native_archive_references(args.result, args.archive) if args.observation is None
                  else verify_binding(args.observation, args.snapshot, args.result, args.archive, args.generated_files))
    except (OSError, ValueError, EOFError, tarfile.TarError, zlib.error) as error:
        print(f"Candidate binding unavailable: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
