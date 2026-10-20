"""Exact native archive journaling and feedback binding for research-loop workers.

The generated SForge script still packages, submits once and prints its native
result. These hooks retain its actual archive before that send. Recovery reads
native history; it never posts an archive or invents an operation identity.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile
import uuid
import urllib.parse
import urllib.request
import zlib

from benchmark.edgebench.research_feedback import (
    archive_manifest, canonical_json, read_archive, validate_packet, write_bytes_atomic,
)

_OPERATION = re.compile(r"[0-9a-f]{32}\Z")
# Native _reserve rejects before allocating a round; this protocol exposes the
# refusal through exact detail strings, without a separate admission error code.
_NATIVE_CAPACITY_REJECTIONS = frozenset({
    "Judge capacity full; retry this snapshot later",
    "Run/task evaluation capacity full; retry this snapshot later",
})


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


def register_probe(root: Path, observation: dict, manifest: dict):
    """Register a completed local probe; no result attribution is added here."""
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 32:
        raise ValueError("invalid_probe_manifest")
    encoded = json.dumps(sorted(files, key=lambda row: row["path"]), sort_keys=True,
                         separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if observation.get("candidate_sha256") != digest or manifest.get("candidate_sha256") != digest:
        raise ValueError("probe_manifest_digest_mismatch")
    if observation.get("result_ref") is not None:
        raise ValueError("probe_registration_must_precede_result_binding")
    value = dict(observation=observation, manifest=manifest)
    identifier = hashlib.sha256(canonical_json(value)).hexdigest()
    with _locked(root):
        _atomic(root / "probes" / (identifier + ".json"), value)
    return "native-probe:" + identifier


def bind_execution_observations(root: Path, packet: dict):
    """Automatically bind matching probe manifests to the *accepted* snapshot.

    Multiple probes of the same source remain independent observations. A probe
    with different selected source, no exact native archive, or an unresolved
    result stays unbound; a changed workspace is never read by this function.
    """
    validate_packet(packet)
    probes = [(path.stem, json.loads(path.read_bytes()))
              for path in sorted((root / "probes").glob("*.json"))]
    bound = []
    for row in packet["records"]:
        if (row.get("attribution_status") != "verified" or not row.get("result_ref")
                or row.get("status") not in {"completed", "error"}):
            continue
        try:
            source = Path(row["archive_path"])
            raw = read_archive(source, root=root)
            if hashlib.sha256(raw).hexdigest() != row["archive_sha256"]:
                continue
            for identifier, probe in probes:
                observation = probe["observation"]
                manifest = probe["manifest"]
                try:
                    digest, files = archive_manifest(raw, [entry["path"] for entry in manifest["files"]])
                except (EOFError, ValueError, gzip.BadGzipFile, tarfile.TarError, zlib.error):
                    # A retained native error archive can be invalid tar. Keep
                    # its generic result usable without attributing a probe.
                    continue
                if (digest != observation["candidate_sha256"] or digest != manifest["candidate_sha256"]
                        or files != manifest["files"]):
                    continue
                observation = dict(observation, result_ref=row["result_ref"], result_candidate_sha256=digest)
                observation["evidence_refs"] = sorted(set([
                    *observation["evidence_refs"], row["result_ref"],
                    "native-archive:" + row["archive_sha256"],
                ]))
                bound.append({**row, "probe_ref": "native-probe:" + identifier,
                              "execution_observation": observation})
        except (KeyError, OSError, ValueError):
            continue
    return bound


def _intents(root):
    return [json.loads(path.read_bytes()) for path in sorted((root / "intents").glob("*.json"))]


def _path(root, operation_id):
    if not _OPERATION.fullmatch(operation_id):
        raise ValueError("invalid_submission_operation")
    return root / "intents" / (operation_id + ".json")


def _reconcile(root, packet):
    validate_packet(packet)
    records = packet["records"]
    intents = _intents(root)
    claimed = {row["submission_id"] for row in intents if row.get("submission_id")}
    for intent in intents:
        if any(intent.get(key) != packet[key] for key in ("run_id", "task_id")):
            raise ValueError("submission_operation_scope_mismatch")
        if intent.get("acceptance") == "rejected" or not intent.get("dispatch_started"):
            continue
        sid = intent.get("submission_id")
        candidates = [row for row in records if row.get("accepted_record") is True
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
                if matched.get("attribution_status") == "verified" and matched.get("result_ref"):
                    intent["result_ref"] = matched["result_ref"]
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
        # Read only the native public history. urllib retains the environment's
        # declared proxy/no_proxy route and never retries a submission.
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


def prepare_submission(root: Path, archive: Path, *, history_reader=None, wait_todo_id=None):
    """Retain native packaged bytes before an effect; recover an unresolved call."""
    with _locked(root):
        packet = read_packet(root)
        intents = _reconcile(root, packet)
        unresolved = [row for row in intents if row.get("acceptance") != "rejected"
                      and not row.get("result_observed")]
        if len(unresolved) > 1:
            raise ValueError("multiple_unresolved_native_operations")
        if unresolved:
            intent = unresolved[0]
            if intent.get("submission_id") and intent.get("acceptance") == "accepted":
                return dict(action="recover", operation_id=intent["operation_id"],
                            submission_id=intent["submission_id"])
            if intent.get("dispatch_started"):
                raise ValueError("native_acceptance_unknown_no_resubmit")
            return dict(action="submit", operation_id=intent["operation_id"], archive_path=intent["archive_path"])
        before = _fresh_history(packet, history_reader=history_reader)
        raw = read_archive(archive.absolute(), root=archive.parent.resolve())
        operation_id = uuid.uuid4().hex
        snapshot = root / "snapshots" / (operation_id + ".tar.gz")
        write_bytes_atomic(snapshot, raw, create_only=True)
        snapshot.chmod(0o400)
        intent = dict(operation_id=operation_id, run_id=packet["run_id"], task_id=packet["task_id"],
                      archive_path=str(snapshot), archive_sha256=hashlib.sha256(raw).hexdigest(),
                      archive_bytes=len(raw), before_submission_ids=before, dispatch_started=False,
                      acceptance="unknown", result_observed=False)
        if wait_todo_id:
            intent["wait_todo_id"] = wait_todo_id
        _atomic(_path(root, operation_id), intent)
        return dict(action="submit", operation_id=operation_id, archive_path=str(snapshot))


def mark_dispatched(root: Path, operation_id: str):
    with _locked(root):
        path = _path(root, operation_id)
        intent = json.loads(path.read_bytes())
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
        elif isinstance(detail, str) and (http_status in {400, 401, 403, 404, 413, 429}
                or (http_status == 503 and detail in _NATIVE_CAPACITY_REJECTIONS)):
            # Only proven native refusals precede admission. Other 5xx, unknown
            # 503 details, parse errors and lost replies remain unresolved.
            intent["acceptance"] = "rejected"
        _atomic(path, intent)
        return intent


def declared_wait_packet(root: Path, intent: dict):
    """An actual accepted response can enter wait before the next host poll.

    This says only that the named operation was accepted. It makes no archive
    or released-result attribution claim; that requires the native observer.
    """
    if intent.get("acceptance") != "accepted" or not intent.get("submission_id"):
        raise ValueError("native_acceptance_unconfirmed")
    packet = read_packet(root)
    if any(intent.get(key) != packet[key] for key in ("run_id", "task_id")):
        raise ValueError("native_acceptance_scope_mismatch")
    records = [row for row in packet["records"] if row.get("submission_id") == intent["submission_id"]]
    if len(records) > 1 or (records and records[0].get("round_id") != intent["round_id"]):
        raise ValueError("native_acceptance_identity_ambiguous")
    if not records:
        packet["records"].append(dict(accepted_record=True, submission_id=intent["submission_id"],
            round_id=intent["round_id"], status="queued", attribution_status="unknown"))
    return packet


def _synchronize_declared_wait(root, intent):
    if intent.get("wait_todo_id") and intent.get("acceptance") == "accepted":
        from .research_feedback_wait import wait_for_feedback
        from .research_feedback_sync import feedback_environment
        wait_for_feedback(feedback_environment(root), todo_id=intent["wait_todo_id"],
            submission_id=intent["submission_id"], packet=declared_wait_packet(root, intent))


def record_result(root: Path, submission_id: str, response: dict):
    if response.get("submission_id") != submission_id:
        raise ValueError("native_result_id_mismatch")
    if response.get("status") not in {"completed", "error"}:
        return
    with _locked(root):
        for intent in _intents(root):
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
    """Fail closed on an unqualified native client, without changing its HTTP API."""
    command = shlex.join([*helper_command, "--root", root])
    replacements = {
        '            echo "  --list, -l      List all previous submissions and scores for this run"':
        '            echo "  --wait-todo ID  Declare that this owned work depends on the accepted feedback"\n'
        '            echo "  --list, -l      List all previous submissions and scores for this run"',
        '        --details|-d) DETAILS_MODE=1 ;;':
        '        --wait-todo)\n            [ "$#" -gt 0 ] && [ -n "$1" ] || exit 1\n'
        '            RESEARCH_WAIT_TODO="$1"; shift ;;\n        --details|-d) DETAILS_MODE=1 ;;',
        'ARCHIVE_SIZE=$(wc -c < "$ARCHIVE_FILE")':
        f'RESEARCH_PREPARED=$({command} prepare --archive "$ARCHIVE_FILE" --wait-todo "${{RESEARCH_WAIT_TODO:-}}")\n'
        'if [ "$(echo "$RESEARCH_PREPARED" | jq -r .action)" = "recover" ]; then\n'
        '    RECOVERED_ID=$(echo "$RESEARCH_PREPARED" | jq -r .submission_id)\n'
        '    if [ "$DETAILS_MODE" = "1" ]; then exec "$0" --result "$RECOVERED_ID" --details; fi\n'
        '    exec "$0" --result "$RECOVERED_ID"\nfi\n'
        'RESEARCH_OPERATION=$(echo "$RESEARCH_PREPARED" | jq -r .operation_id)\n'
        'ARCHIVE_FILE=$(echo "$RESEARCH_PREPARED" | jq -r .archive_path)\n'
        'ARCHIVE_SIZE=$(wc -c < "$ARCHIVE_FILE")',
        'if ! HTTP_CODE=$(curl -sS -o "$REQUEST_TMP/submit.json"':
        f'{command} dispatched --operation-id "$RESEARCH_OPERATION" >/dev/null\n'
        'if ! HTTP_CODE=$(curl -sS -o "$REQUEST_TMP/submit.json"',
        'SUBMIT_RESP=$(cat "$REQUEST_TMP/submit.json")':
        'SUBMIT_RESP=$(cat "$REQUEST_TMP/submit.json")\n'
        f'{command} response --operation-id "$RESEARCH_OPERATION" --http-status "$HTTP_CODE" '
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
    parser.add_argument("--root", type=Path, default=os.environ.get("LOOPX_RESEARCH_FEEDBACK_ROOT"))
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--archive", type=Path, required=True)
    prepare.add_argument("--wait-todo")
    dispatched = sub.add_parser("dispatched")
    dispatched.add_argument("--operation-id", required=True)
    response = sub.add_parser("response")
    response.add_argument("--operation-id", required=True)
    response.add_argument("--http-status", type=int, required=True)
    response.add_argument("--response-file", type=Path, required=True)
    result = sub.add_parser("result")
    result.add_argument("--submission-id", required=True)
    result.add_argument("--response-file", type=Path, required=True)
    sub.add_parser("sync")
    args = parser.parse_args(argv)
    if args.root is None:
        parser.error("native research feedback root is required")
    try:
        if args.action == "prepare":
            value = prepare_submission(args.root, args.archive, wait_todo_id=args.wait_todo)
            if value["action"] == "recover":
                _synchronize_declared_wait(args.root, json.loads(_path(args.root, value["operation_id"]).read_bytes()))
        elif args.action == "dispatched":
            value = mark_dispatched(args.root, args.operation_id)
        elif args.action == "response":
            try:
                payload = json.loads(args.response_file.read_bytes())
            except (ValueError, OSError):
                payload = {}
            value = record_response(args.root, args.operation_id, args.http_status, payload)
            _synchronize_declared_wait(args.root, value)
        elif args.action == "result":
            record_result(args.root, args.submission_id, json.loads(args.response_file.read_bytes()))
            return 0
        else:
            packet = read_packet(args.root)
            value = dict(packet=packet, intents=synchronize_intents(args.root, packet),
                         observations=bind_execution_observations(args.root, packet))
        print(json.dumps(value, allow_nan=False))
        return 0
    except Exception as error:
        # Transport exceptions may contain credential-bearing URLs. Expose only
        # controlled validation codes or their type, never the raw exception.
        reason = str(error) if type(error) is ValueError else type(error).__name__
        print("Research submission halted: " + reason + "; no automatic resubmission.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
