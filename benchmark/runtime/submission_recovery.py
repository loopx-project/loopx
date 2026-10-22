"""Retain and recover one native SForge submission without repeating its POST.

The native client still owns packaging, submission and synchronous result reads.
Only the exact packaged snapshot, operation and accepted native identity are
retained here. This adapter does not create research work or control model calls.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import uuid
import urllib.parse
import urllib.request

from benchmark.runtime.submission_receipts import (
    canonical_json, read_archive, validate_packet, write_bytes_atomic,
)

_OPERATION = re.compile(r"[0-9a-f]{32}\Z")
# The pinned provider has no typed rejection codes. Match only its explicit
# pre-admission reasons: its broad 401/404 handlers also cover errors after a
# round and submission identity have been allocated, so HTTP status alone is
# never proof that a later invocation may submit again.
_NATIVE_CAPACITY_REJECTIONS = frozenset({
    "Judge capacity full; retry this snapshot later",
    "Run/task evaluation capacity full; retry this snapshot later",
})


def _known_native_rejection(http_status, detail):
    if not isinstance(detail, str):
        return False
    if http_status == 400:
        return detail == "kind must be 'agent' or 'auto'"
    if http_status == 403:
        return detail == "admin_secret required for auto submissions"
    if http_status == 503:
        return detail in _NATIVE_CAPACITY_REJECTIONS
    if http_status == 429:
        return (re.fullmatch(r"Submission budget exhausted \(([0-9]+)/\1 used\)", detail) is not None
                or re.fullmatch(r"Submission cooldown active — wait [1-9][0-9]*s before next submission\. "
                                r"Continue working on improvements and submit again later\.", detail) is not None)
    return False


def _atomic(path, value):
    write_bytes_atomic(path, canonical_json(value))


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "submission.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def read_packet(root: Path):
    return validate_packet(json.loads((root / "native.json").read_bytes()))


def _intents(root):
    return [json.loads(path.read_bytes()) for path in sorted((root / "intents").glob("*.json"))]


def _path(root, operation_id):
    if not _OPERATION.fullmatch(operation_id):
        raise ValueError("invalid_submission_operation")
    return root / "intents" / (operation_id + ".json")


def _reconcile(root, packet):
    validate_packet(packet)
    intents = _intents(root)
    claimed = {row["submission_id"] for row in intents if row.get("submission_id")}
    for intent in intents:
        if any(intent.get(key) != packet[key] for key in ("run_id", "task_id")):
            raise ValueError("submission_operation_scope_mismatch")
        if (intent.get("retired_before_dispatch") or intent.get("acceptance") == "rejected"
                or not intent.get("dispatch_started")):
            continue
        sid = intent.get("submission_id")
        candidates = [row for row in packet["records"] if row.get("accepted_record") is True
            and row.get("submission_id") not in intent["before_submission_ids"]
            and row.get("archive_sha256") == intent["archive_sha256"]
            and (row.get("submission_id") == sid if sid else row.get("submission_id") not in claimed)]
        if len(candidates) == 1:
            matched = candidates[0]
            if sid and intent.get("round_id") != matched["round_id"]:
                intent["recovery_reason"] = "native_round_identity_changed"
            else:
                intent.update(acceptance="accepted", submission_id=matched["submission_id"],
                              round_id=matched["round_id"], recovered=not bool(sid) or intent.get("recovered", False))
                claimed.add(matched["submission_id"])
                intent.pop("recovery_reason", None)
        else:
            intent["recovery_reason"] = "ambiguous_native_archive_match" if candidates else "native_archive_match_unavailable"
        _atomic(_path(root, intent["operation_id"]), intent)
    return intents


def synchronize_intents(root: Path, packet: dict):
    with _locked(root):
        return _reconcile(root, packet)


def _fresh_history(packet, *, history_reader=None):
    if history_reader is not None:
        history = history_reader()
    else:
        endpoint = os.environ.get("SFORGE_JUDGE_URL", "").rstrip("/")
        token = os.environ.get("SFORGE_TOKEN")
        if not endpoint or not token:
            raise ValueError("native_registration_environment_missing")
        query = urllib.parse.urlencode({"token": token})
        # Honor the native client's declared proxy route; this is a read only.
        with urllib.request.urlopen(endpoint + "/api/v1/history?" + query, timeout=15) as response:
            history = json.load(response)
    if history.get("run_id") != packet["run_id"] or not isinstance(history.get("entries"), list):
        raise ValueError("native_history_scope_mismatch")
    entries = [row for row in history["entries"] if row.get("type") == "submission"]
    if any(row.get("task_id") != packet["task_id"] for row in entries):
        raise ValueError("native_history_task_mismatch")
    ids = [row.get("submission_id") for row in entries]
    if any(not isinstance(sid, str) or not sid for sid in ids) or len(ids) != len(set(ids)):
        raise ValueError("native_history_identity_ambiguous")
    return ids


def prepare_submission(root: Path, archive: Path, *, history_reader=None):
    """Prepare current bytes, or recover an operation that may have been sent."""
    with _locked(root):
        packet = read_packet(root)
        intents = _reconcile(root, packet)
        unresolved = [row for row in intents if not row.get("retired_before_dispatch")
                      and row.get("acceptance") != "rejected" and not row.get("result_observed")]
        if len(unresolved) > 1:
            raise ValueError("multiple_unresolved_native_operations")
        previous = unresolved[0] if unresolved else None
        if previous is not None:
            if previous.get("submission_id") and previous.get("acceptance") == "accepted":
                return dict(action="recover", operation_id=previous["operation_id"],
                            submission_id=previous["submission_id"], archive_sha256=previous["archive_sha256"])
            if previous.get("dispatch_started"):
                raise ValueError("native_acceptance_unknown_no_resubmit operation="
                    + previous["operation_id"] + " snapshot=" + previous["archive_sha256"])
        # Canonicalize only the owned temporary parent: resolving the leaf
        # would silently accept an archive symlink.
        parent = archive.parent.resolve(strict=True)
        raw = read_archive(parent / archive.name, root=parent)
        digest = hashlib.sha256(raw).hexdigest()
        if previous is not None and previous["archive_sha256"] == digest:
            return dict(action="submit", operation_id=previous["operation_id"],
                        archive_path=previous["archive_path"], archive_sha256=digest)
        before = _fresh_history(packet, history_reader=history_reader)
        operation_id = uuid.uuid4().hex
        snapshot = root / "snapshots" / (operation_id + ".tar.gz")
        write_bytes_atomic(snapshot, raw, create_only=True)
        snapshot.chmod(0o400)
        if previous is not None:
            # Retire first, then install the replacement, under the dispatch
            # lock. A crash between these writes leaves no sendable old work;
            # a later call can prepare current bytes again. A late old client
            # must fail mark_dispatched instead of sending its stale snapshot.
            previous["retired_before_dispatch"] = True
            _atomic(_path(root, previous["operation_id"]), previous)
        intent = dict(operation_id=operation_id, run_id=packet["run_id"], task_id=packet["task_id"],
                      archive_path=str(snapshot), archive_sha256=digest,
                      archive_bytes=len(raw), before_submission_ids=before, dispatch_started=False,
                      acceptance="unknown", result_observed=False)
        _atomic(_path(root, operation_id), intent)
        return dict(action="submit", operation_id=operation_id, archive_path=str(snapshot), archive_sha256=digest)


def mark_dispatched(root: Path, operation_id: str):
    with _locked(root):
        path = _path(root, operation_id)
        intent = json.loads(path.read_bytes())
        if intent.get("retired_before_dispatch"):
            raise ValueError("native_prepared_operation_retired")
        if intent["dispatch_started"]:
            raise ValueError("native_operation_already_dispatched")
        raw = read_archive(Path(intent["archive_path"]), root=root)
        if hashlib.sha256(raw).hexdigest() != intent["archive_sha256"]:
            raise ValueError("prepared_native_archive_changed")
        intent["dispatch_started"] = True
        _atomic(path, intent)
        return intent


def record_response(root: Path, operation_id: str, http_status: int, response: dict):
    with _locked(root):
        path = _path(root, operation_id)
        intent = json.loads(path.read_bytes())
        if not intent.get("dispatch_started"):
            raise ValueError("native_operation_not_dispatched")
        sid, round_id = response.get("submission_id"), response.get("round_id")
        detail = response.get("detail")
        if (http_status == 200 and isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid)
                and isinstance(round_id, str) and re.fullmatch(r"agent-[1-9][0-9]*", round_id)
                and sid not in intent["before_submission_ids"]):
            if intent.get("submission_id") not in (None, sid) or intent.get("round_id") not in (None, round_id):
                raise ValueError("native_acceptance_receipt_changed")
            intent.update(acceptance="accepted", submission_id=sid, round_id=round_id)
        elif _known_native_rejection(http_status, detail):
            if intent.get("acceptance") == "accepted":
                raise ValueError("native_acceptance_receipt_changed")
            intent["acceptance"] = "rejected"
        _atomic(path, intent)
        return intent


def record_result(root: Path, submission_id: str, response: dict):
    if response.get("submission_id") != submission_id:
        raise ValueError("native_result_id_mismatch")
    if response.get("status") not in {"completed", "error"}:
        return
    with _locked(root):
        # A caller may use the native --result ID directly after a lost reply.
        # Only the accepted-archive bridge can bind that ID to the old intent;
        # the result response alone cannot attribute an unknown operation.
        for intent in _reconcile(root, read_packet(root)):
            if intent.get("submission_id") == submission_id and intent.get("acceptance") == "accepted":
                report = response.get("report")
                if response["status"] == "completed" and (not isinstance(report, dict)
                        or report.get("submission_id") != submission_id
                        or report.get("task_id") != intent["task_id"]):
                    raise ValueError("native_completed_result_scope_mismatch")
                if response["status"] == "error" and report is not None:
                    raise ValueError("native_error_result_ambiguous")
                intent["result_observed"] = True
                _atomic(_path(root, intent["operation_id"]), intent)


def instrument_submit_script(script: str, helper_command: list[str], root: str) -> str:
    """Journal the pinned native client's existing package/send/result edges."""
    command = shlex.join([*helper_command, "--root", root])
    replacements = {
        'ARCHIVE_SIZE=$(wc -c < "$ARCHIVE_FILE")':
        f'RECOVERY_PREPARED=$({command} prepare --archive "$ARCHIVE_FILE")\n'
        'if [ "$(echo "$RECOVERY_PREPARED" | jq -r .action)" = "recover" ]; then\n'
        '    RECOVERED_ID=$(echo "$RECOVERY_PREPARED" | jq -r .submission_id)\n'
        '    rm -rf "$REQUEST_TMP"\n'
        '    if [ "$DETAILS_MODE" = "1" ]; then exec "$0" --result "$RECOVERED_ID" --details; fi\n'
        '    exec "$0" --result "$RECOVERED_ID"\nfi\n'
        'RECOVERY_OPERATION=$(echo "$RECOVERY_PREPARED" | jq -r .operation_id)\n'
        'ARCHIVE_FILE=$(echo "$RECOVERY_PREPARED" | jq -r .archive_path)\n'
        'ARCHIVE_SIZE=$(wc -c < "$ARCHIVE_FILE")',
        'if ! HTTP_CODE=$(curl -sS -o "$REQUEST_TMP/submit.json"':
        f'{command} dispatched --operation-id "$RECOVERY_OPERATION" >/dev/null\n'
        'if ! HTTP_CODE=$(curl -sS -o "$REQUEST_TMP/submit.json"',
        'SUBMIT_RESP=$(cat "$REQUEST_TMP/submit.json")':
        'SUBMIT_RESP=$(cat "$REQUEST_TMP/submit.json")\n'
        f'{command} response --operation-id "$RECOVERY_OPERATION" --http-status "$HTTP_CODE" '
        '--response-file "$REQUEST_TMP/submit.json" >/dev/null',
        '    RESULT=$(get_json "$JUDGE_URL/api/v1/result/$SUBMISSION_ID")':
        '    RESULT=$(get_json "$JUDGE_URL/api/v1/result/$SUBMISSION_ID")\n'
        f'    {command} result --submission-id "$SUBMISSION_ID" --response-file "$REQUEST_TMP/response.json"',
    }
    for anchor, replacement in replacements.items():
        if script.count(anchor) != 1:
            raise ValueError("unsupported_native_submit_script_boundary")
        script = script.replace(anchor, replacement, 1)
    return script


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--archive", type=Path, required=True)
    dispatched = commands.add_parser("dispatched")
    dispatched.add_argument("--operation-id", required=True)
    response = commands.add_parser("response")
    response.add_argument("--operation-id", required=True)
    response.add_argument("--http-status", type=int, required=True)
    response.add_argument("--response-file", type=Path, required=True)
    result = commands.add_parser("result")
    result.add_argument("--submission-id", required=True)
    result.add_argument("--response-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            value = prepare_submission(args.root, args.archive)
            if value["action"] == "recover":
                print("Recovering original native operation=" + value["operation_id"]
                    + " submission=" + value["submission_id"] + " snapshot=" + value["archive_sha256"]
                    + "; reading its archived candidate's result. No new candidate is submitted.", file=sys.stderr)
        elif args.action == "dispatched":
            value = mark_dispatched(args.root, args.operation_id)
        elif args.action == "response":
            try:
                payload = json.loads(args.response_file.read_bytes())
            except (ValueError, OSError):
                payload = {}
            value = record_response(args.root, args.operation_id, args.http_status, payload)
        else:
            record_result(args.root, args.submission_id, json.loads(args.response_file.read_bytes()))
            return 0
        print(json.dumps(value, allow_nan=False))
        return 0
    except Exception as error:
        # Transport errors may embed credential-bearing URLs. Only controlled
        # validation reasons or the error type may enter the native terminal.
        reason = str(error) if type(error) is ValueError else type(error).__name__
        print("Native submission halted: " + reason + "; no automatic resubmission.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
