"""Passive whole-run cost readback over existing private research artifacts.

The TypeScript trajectory owner interprets Codex counters. This adapter only
selects non-overlapping complete captures and reports observed counts; it does
not price tokens, divide costs between stages, or infer missing consumption.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re


def _json(path):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def _coverage(known, missing):
    return "partial" if known and missing else "complete" if known else "unknown"


def _collection(trial):
    rows = _json(trial / "collected" / "artifact-collection.json")
    return {row["artifact"] for row in rows if isinstance(row, dict)
            and isinstance(row.get("artifact"), str) and row.get("collected") is True} \
        if isinstance(rows, list) else set()


def _requests(trial, collected):
    paths = sorted((trial / "collected" / "runtime").rglob("provider-requests.jsonl"))
    grouped, malformed, readable = {}, 0, 0
    for path in paths:
        try:
            if path.resolve() != path.absolute():
                raise ValueError("nonphysical capture")
            lines = path.read_text(encoding="utf-8").splitlines()
            readable += 1
            for line in lines:
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or not isinstance(row.get("request_id"), str)
                            or not row["request_id"] or row.get("event") not in {"dispatch", "finished"}):
                        raise ValueError("invalid request record")
                    grouped.setdefault(row["request_id"], []).append(row)
                except (ValueError, TypeError):
                    malformed += 1
        except (OSError, UnicodeError, ValueError):
            malformed += 1
    counts = {"forwarded": 0, "not_forwarded": 0, "send_unknown": 0}
    for rows in grouped.values():
        finished = {json.dumps(row, sort_keys=True, separators=(",", ":")):
                    row for row in rows if row["event"] == "finished"}
        value = next(iter(finished.values())) if len(finished) == 1 else {}
        forwarded = value.get("forwarded")
        if forwarded is True:
            counts["forwarded"] += 1
        elif forwarded is False and not any(row["event"] == "dispatch" for row in rows):
            counts["not_forwarded"] += 1
        else:
            counts["send_unknown"] += 1
    return {"capture_coverage": _coverage(readable, malformed or "runtime" not in collected),
            "captured_distinct_requests": len(grouped) if readable else None,
            **(counts if readable else {key: None for key in counts}), "invalid_records": malformed,
            "scope": "observed_gateway_requests_only"}


def _prefix(shorter, longer):
    with shorter.open("rb") as left, longer.open("rb") as right:
        while chunk := left.read(1024 * 1024):
            if chunk != right.read(len(chunk)):
                return False
    return True


def _tokens(trial, collected):
    from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import _usage_capture

    root = trial / "collected"
    paths = {path for name in ("native-sessions", "worker-sessions") for path in (root / name).rglob("*.jsonl")}
    paths.update(path for path in (root / "runtime").rglob("*.jsonl")
                 if "sessions" in path.relative_to(root / "runtime").parts)
    grouped, invalid = {}, 0
    for path in sorted(paths):
        try:
            if path.resolve() != path.absolute():
                raise ValueError("nonphysical session capture")
            with path.open("rb") as stream:
                header = json.loads(stream.readline())
                payload = header.get("payload") or {}
                session = payload.get("id")
                if header.get("type") != "session_meta" or not isinstance(session, str) or not session:
                    raise ValueError("missing native session identity")
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            grouped.setdefault(session, []).append((path, path.stat().st_size, digest))
        except (OSError, ValueError, AttributeError):
            invalid += 1
    fields = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
    totals, qualified, unknown, duplicates = dict.fromkeys(fields, 0), 0, invalid, 0
    for session, captures in grouped.items():
        longest, _, digest = max(captures, key=lambda row: row[1])
        try:
            # Same identity alone does not make divergent/forked snapshots one
            # charge. Only byte-identical or strict-prefix captures are deduped.
            if not all(row[2] == digest or _prefix(row[0], longest) for row in captures):
                raise ValueError("divergent session captures")
            result = _usage_capture(root, longest.relative_to(root).as_posix(), session)
            if result["artifact_sha256"] != digest:
                raise ValueError("session moved during readback")
            projection = result["projection"]
            for field in fields:
                totals[field] += projection[field]
            qualified += 1
            duplicates += len(captures) - 1
        except (OSError, ValueError, RuntimeError):
            # Missing prefixes, forks, gaps and unfinished Turns are rejected
            # by the existing typed owner. Never substitute the maximum count.
            unknown += 1
    complete_collection = {"runtime", "native-sessions", "worker-sessions"}.issubset(collected)
    return {"capture_coverage": _coverage(qualified, unknown or not complete_collection),
            "whole_run_coverage": "unknown", "qualified_sessions": qualified,
            "unknown_sessions_or_files": unknown, "duplicate_or_prefix_captures": duplicates,
            "known_captured_totals": ({**totals, "input_plus_output_tokens":
                totals["input_tokens"] + totals["output_tokens"]} if qualified else None),
            "method": "codex_complete_session_counters_v1",
            "limitations": "Known complete captured sessions only. Input includes cached input; output includes reasoning. Missing/provider-side consumption and billing remain unknown."}


def _submissions(trial):
    from .research_feedback import validate_packet

    packet = _json(trial / "native-research-feedback" / "native.json")
    final = _json(trial / "final_result.json")
    native_total = final.get("total_rounds") if isinstance(final, dict) else None
    if type(native_total) is not int or native_total < 0:
        native_total = None
    try:
        validate_packet(packet)
    except (ValueError, TypeError):
        return {"capture_coverage": "unknown", "manual_accepted_count": None,
                "first_accepted": None, "native_total_rounds": native_total}
    grouped, unknown = {}, 0
    for row in packet["records"]:
        if (not isinstance(row, dict) or row.get("accepted_record") is not True
                or not isinstance(row.get("submission_id"), str) or not row["submission_id"]
                or not isinstance(row.get("round_id"), str)
                or not re.fullmatch(r"agent-[1-9][0-9]*", row["round_id"])):
            unknown += 1
            continue
        grouped.setdefault(row["submission_id"], []).append(row)
    accepted = []
    for rows in grouped.values():
        if len({json.dumps(row, sort_keys=True) for row in rows}) != 1:
            unknown += 1
        else:
            accepted.append(rows[0])
    rounds = [row["round_id"] for row in accepted]
    unique = [row for row in accepted if rounds.count(row["round_id"]) == 1]
    unknown += len(accepted) - len(unique)
    ordered = sorted(unique, key=lambda row: int(row["round_id"].split("-")[1]))
    first = ordered[0] if ordered and ordered[0]["round_id"] == "agent-1" else None
    released = first and first.get("attribution_status") == "verified" and first.get("result_ref")
    summary = first.get("result_summary") if released else None
    safe_summary = {key: summary[key] for key in ("score", "pass_rate", "valid", "passed", "failed", "total_tests")
                    if isinstance(summary, dict) and key in summary and (summary[key] is None
                        or type(summary[key]) is bool or type(summary[key]) in (int, float) and math.isfinite(summary[key]))}
    return {"capture_coverage": "partial" if unknown else "complete", "manual_accepted_count": len(unique),
            "first_accepted": ({"acceptance_ordinal": 1, "released": bool(released),
                "result_summary": safe_summary if released else None}
                if first else None), "native_total_rounds": native_total}


def summarize_research_cost(trial: Path, runner_elapsed_seconds):
    """No new I/O to models/judges; report only already captured local evidence."""
    trial = Path(trial)
    elapsed = runner_elapsed_seconds if type(runner_elapsed_seconds) in (int, float) \
        and math.isfinite(runner_elapsed_seconds) and runner_elapsed_seconds >= 0 else None
    collected = _collection(trial)
    return {"schema_version": "edgebench_research_cost_v0", "runner_wall_seconds": elapsed,
            "wall_scope": "measured_runner_entry_through_native_cleanup_and_final_result",
            "stage_seconds": None, "stage_allocation_coverage": "unknown", "billing_usd": None,
            "provider_requests": _requests(trial, collected), "tokens": _tokens(trial, collected),
            "evaluations": _submissions(trial)}
