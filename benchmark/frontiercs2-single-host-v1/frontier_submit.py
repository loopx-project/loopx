#!/usr/bin/env python3
"""Submit task-defined tar archives to the run-local Frontier Driver."""

from __future__ import annotations

import uuid
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import time
from urllib.parse import urlsplit


SCHEMA = "frontier.candidate-submit-result/v1"
TOKEN_LIMIT = 16 * 1024
RESPONSE_LIMIT = 64 * 1024 * 1024
REQUEST_ATTEMPTS = 16
MAX_MEMBERS = 100_000
FINAL_RECEIPT = Path("/workspace/.frontier/final-submission.json")
TERMINAL = {"passed", "failed"}
PENDING = {"queued", "running"}
VERIFICATION_ID = re.compile(r"verification_[A-Za-z0-9]{1,64}")


class SubmitError(RuntimeError):
    pass


def validate_tar(path: Path) -> tuple[int, str]:
    if not path.is_absolute():
        raise SubmitError("archive path must be absolute")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise SubmitError("archive must be a nonempty regular file")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_MEMBERS:
                raise SubmitError("archive has an invalid member count")
            seen: dict[str, bool] = {}
            for member in members:
                name = PurePosixPath(member.name)
                normalized = name.as_posix()
                if (
                    not normalized
                    or normalized == "."
                    or member.name.startswith("/")
                    or "//" in member.name
                    or ".." in name.parts
                    or member.isdev()
                    or member.isfifo()
                ):
                    raise SubmitError(f"unsafe archive member: {member.name}")
                if normalized in seen:
                    raise SubmitError(f"duplicate archive member: {member.name}")
                for parent in name.parents:
                    parent_name = parent.as_posix()
                    if parent_name != "." and parent_name in seen and not seen[parent_name]:
                        raise SubmitError(f"conflicting archive member: {member.name}")
                if not member.isdir() and any(existing.startswith(f"{normalized}/") for existing in seen):
                    raise SubmitError(f"conflicting archive member: {member.name}")
                seen[normalized] = member.isdir()
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname)
                    if member.linkname.startswith("/") or ".." in target.parts:
                        raise SubmitError(f"unsafe archive link: {member.name}")
    except tarfile.TarError as error:
        raise SubmitError("archive must be an uncompressed POSIX tar") from error
    return metadata.st_size, f"sha256:{digest.hexdigest()}"


def token() -> str:
    path = Path(os.environ.get("FRONTIER_DRIVER_TOKEN_FILE", ""))
    if not path.is_absolute():
        raise SubmitError("FRONTIER_DRIVER_TOKEN_FILE must be absolute")
    value = path.read_bytes()
    if not value or len(value) > TOKEN_LIMIT:
        raise SubmitError("Driver token file has an invalid size")
    return value.decode("utf-8").strip()


class Client:
    def __init__(self) -> None:
        parsed = urlsplit(os.environ.get("FRONTIER_DRIVER_API_URL", ""))
        if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
            raise SubmitError("FRONTIER_DRIVER_API_URL must be a plain HTTP origin")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.auth = token()

    def request(self, method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None) -> dict[str, object]:
        request_headers = {"Authorization": f"Bearer {self.auth}"}
        request_headers.update(headers or {})
        for attempt in range(REQUEST_ATTEMPTS):
            connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
            try:
                connection.request(method, path, body=body, headers=request_headers)
                response = connection.getresponse()
                data = response.read(RESPONSE_LIMIT + 1)
                if len(data) > RESPONSE_LIMIT:
                    raise SubmitError("Driver response exceeded its size limit")
                if response.status in {408, 429, 500, 502, 503, 504} and attempt + 1 < REQUEST_ATTEMPTS:
                    time.sleep(min(10, 0.25 * (2 ** attempt)))
                    continue
                if response.status < 200 or response.status >= 300:
                    raise SubmitError(f"Driver returned HTTP {response.status}")
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise SubmitError("Driver response must be a JSON object")
                return value
            except (OSError, http.client.HTTPException) as error:
                if attempt + 1 == REQUEST_ATTEMPTS:
                    raise SubmitError("Driver request failed") from error
                time.sleep(min(10, 0.25 * (2 ** attempt)))
            finally:
                connection.close()
        raise SubmitError("Driver request failed")


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def idempotency(operation: str, digest: str) -> str:
    return f"frontier-submit-{operation}-{digest.removeprefix('sha256:')}"


def upload(client: Client, operation: str, path: Path) -> tuple[str, str]:
    size, digest = validate_tar(path)
    value = client.request("POST", "/v1/payloads", body=path.read_bytes(), headers={
        "Content-Type": "application/x-tar", "Content-Length": str(size),
        "X-Content-SHA256": digest, "X-Frontier-Purpose": operation,
        "Idempotency-Key": idempotency(f"payload-{operation}", digest),
    })
    payload_id = value.get("payload_id")
    if not isinstance(payload_id, str) or not payload_id.startswith("payload_"):
        raise SubmitError("Driver payload response is invalid")
    return payload_id, digest


def train_result(view: dict[str, object], *, payload_id: str, verification_id: str,
                 remaining: int) -> tuple[dict[str, object], int]:
    """The Agent-facing JSON and exit status for one train verification view."""
    state = view.get("state")
    if state not in TERMINAL and state not in PENDING:
        raise SubmitError("Driver train response has an invalid state")
    result: dict[str, object] = {
        "schema_version": SCHEMA, "operation": "train", "state": state,
        "payload_id": payload_id, "verification_id": verification_id,
        "remaining_train_calls": remaining,
    }
    if state in PENDING:
        return result, 0
    metrics = view.get("metrics", {})
    if not isinstance(metrics, dict):
        raise SubmitError("Driver train response has invalid metrics")
    result["metrics"] = metrics
    failure = view.get("failure")
    if state == "failed" and failure is not None:
        if (not isinstance(failure, dict)
                or not isinstance(failure.get("code"), str)
                or not isinstance(failure.get("message"), str)
                or not isinstance(failure.get("retryable"), bool)):
            raise SubmitError("Driver train response has invalid failure feedback")
        result["failure"] = {key: failure[key] for key in ("code", "message", "retryable")}

    if isinstance(view.get("raw_score"), (int, float)):
        result["raw_score"] = view["raw_score"]
    return result, 0 if state == "passed" else 2


def run_train(client: Client, archive: Path) -> tuple[dict[str, object], int]:
    payload_id, digest = upload(client, "train", archive)
    view = client.request("POST", "/v1/verifier-train", body=canonical({"payload_id": payload_id}), headers={
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency("train", digest),
    })
    verification_id = view.get("verification_id")
    remaining = view.get("remaining_train_calls")
    if not isinstance(verification_id, str) or not isinstance(remaining, int) or remaining < 0:
        raise SubmitError("Driver train response lacks remaining_train_calls")
    return train_result(view, payload_id=payload_id, verification_id=verification_id, remaining=remaining)


def run_status(client: Client, verification_id: str) -> tuple[dict[str, object], int]:
    """Read one train verification once, in the same JSON shape as `train`."""
    if not VERIFICATION_ID.fullmatch(verification_id):
        raise SubmitError("invalid verification id")
    view = client.request("GET", f"/v1/verifications/{verification_id}")
    if view.get("verification_id") != verification_id or view.get("mode") != "train":
        raise SubmitError("status reports train verifications only")
    payload_id = view.get("payload_id")
    remaining = view.get("remaining_train_calls")
    if not isinstance(payload_id, str) or type(remaining) is not int or remaining < 0:
        raise SubmitError("Driver verification response lacks payload_id or remaining_train_calls")
    return train_result(view, payload_id=payload_id, verification_id=verification_id, remaining=remaining)


def run_store_final(client: Client, archive: Path) -> dict[str, object]:
    payload_id, _ = upload(client, "final", archive)
    # One intent per invocation, retained across the Client's transport retries.
    # A deliberate later invocation may select the same tar again.
    view = client.request("POST", "/v1/submissions/checkpoint", body=canonical({"payload_id": payload_id}), headers={
        "Content-Type": "application/json",
        "Idempotency-Key": f"frontier-store-final-{uuid.uuid4()}",
    })
    runtime = view.get("remaining_runtime_s")
    remaining = view.get("remaining_checkpoint_submissions")
    if (type(runtime) is not int or runtime < 0 or "remaining_checkpoint_submissions" not in view
            or (remaining is not None and (type(remaining) is not int or remaining < 0))):
        raise SubmitError("Driver store-final response has invalid remaining runtime or submission count")
    return {"schema_version": SCHEMA, "operation": "store-final", "state": "stored",
            "payload_id": payload_id, "remaining_runtime_s": runtime,
            "remaining_checkpoint_submissions": remaining}


def run_final(client: Client, archive: Path) -> dict[str, object]:
    payload_id, digest = upload(client, "final", archive)
    view = client.request("POST", "/v1/submissions", body=canonical({"payload_id": payload_id}), headers={
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency("final", digest),
    })
    verification_id = view.get("verification_id")
    if not isinstance(verification_id, str):
        raise SubmitError("Driver submission response is invalid")
    result = {"schema_version": SCHEMA, "operation": "final", "state": "accepted",
              "payload_id": payload_id, "verification_id": verification_id}
    FINAL_RECEIPT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(FINAL_RECEIPT, flags, 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(canonical(result)); destination.flush(); os.fsync(destination.fileno())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)
    train = operations.add_parser("train", help="request train feedback for a tar")
    train.add_argument("archive", type=Path)
    for name in ("final", "store-final"):
        operations.add_parser(name).add_argument("archive", type=Path)
    operations.add_parser("status", help="read one train verification").add_argument("verification_id")
    args = parser.parse_args(argv)
    try:
        client = Client()
        if args.operation == "train":
            result, status = run_train(client, args.archive)
        elif args.operation == "status":
            result, status = run_status(client, args.verification_id)
        elif args.operation == "store-final":
            result, status = run_store_final(client, args.archive), 0
        else:
            result, status = run_final(client, args.archive), 0
        sys.stdout.buffer.write(canonical(result))
        return status
    except (SubmitError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"frontier-submit: ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
