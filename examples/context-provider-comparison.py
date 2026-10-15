#!/usr/bin/env python3
"""Compare explicit providers through the real, read-only Decision Context CLI.

The suite/report schemas are local experiment formats, not LoopX authority
contracts. Exact returned-content hashes determine relevance; provider scores
are neither calibrated across providers nor interpreted as answer confidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any


def content_hash(text: str) -> str:
    canonical = text.replace("\r\n", "\n").removesuffix("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_path(parent: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else parent / path


def load_suite(path: Path) -> tuple[dict[str, Any], dict[str, str], str]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != "context_provider_comparison_suite_v0":
        raise ValueError("unsupported comparison suite")
    corpus_bytes = resolve_path(path.parent, suite["corpus"]).read_bytes()
    corpus = json.loads(corpus_bytes)
    hashes: dict[str, str] = {}
    ids: set[str] = set()
    for document in corpus["documents"]:
        doc_id = document["id"]
        digest = content_hash(document["content"])
        if doc_id in ids or digest in hashes:
            raise ValueError("corpus document identities/content must be unique")
        ids.add(doc_id)
        hashes[digest] = doc_id
    seen: set[str] = set()
    for query in suite["queries"]:
        if (
            query["id"] in seen
            or not isinstance(query["query"], str)
            or not query["query"].strip()
        ):
            raise ValueError("query identities must be unique and text nonempty")
        seen.add(query["id"])
        relevant = query["relevant_ids"]
        if (
            not isinstance(relevant, list)
            or len(relevant) != len(set(relevant))
            or not set(relevant) <= ids
        ):
            raise ValueError("qrels must name unique corpus documents")
        if not relevant and query.get("unanswerable") is not True:
            raise ValueError("empty qrels require an explicit unanswerable case")
    provider_ids = [provider["id"] for provider in suite["providers"]]
    if not provider_ids or len(provider_ids) != len(set(provider_ids)):
        raise ValueError("provider identities must be unique and nonempty")
    limit = suite.get("max_results", 3)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
        raise ValueError("max_results must be between 1 and 8")
    return suite, hashes, hashlib.sha256(corpus_bytes).hexdigest()


def assess(
    packet: dict[str, Any], hashes: dict[str, str], relevant: list[str], limit: int
) -> dict[str, Any]:
    if (
        packet.get("schema_version") != "decision_context_ephemeral_recall_v0"
        or packet.get("visibility") != "local_private_transient"
        or packet.get("authority") != "advisory_only"
        or packet.get("execution_authorized") is not False
    ):
        raise ValueError("invalid native recall boundary")
    if packet.get("status") != "completed":
        return {
            "status": "unavailable",
            "reason_code": packet.get("reason_code") or "provider_unavailable",
        }
    items = packet["results"]
    if not isinstance(items, list) or len(items) > limit:
        raise ValueError("invalid native result bounds")
    ranked_ids: list[str | None] = []
    chars = 0
    for item in items:
        if (
            item.get("content_may_instruct") is not False
            or item.get("content_trust") != "untrusted_advisory"
        ):
            raise ValueError("invalid native content trust")
        content = item["content"]
        if not isinstance(content, str):
            raise ValueError("invalid result content")
        ranked_ids.append(hashes.get(content_hash(content)))
        chars += len(content)
    # Keep duplicate/unknown results in their real rank positions. A duplicate
    # cannot increase recall; no provider-private URI is used as ground truth.
    found = set(ranked_ids) & set(relevant)
    first = next(
        (rank for rank, value in enumerate(ranked_ids, 1) if value in relevant), None
    )
    receipt = packet.get("retrieval_receipt") or {}
    return {
        "status": "completed",
        "provider_version": packet.get("provider_version"),
        "ranked_document_ids": ranked_ids,
        "unmatched_result_count": ranked_ids.count(None),
        "returned_count": len(items),
        "returned_content_chars": chars,
        "retrieval_latency_ms": (receipt.get("telemetry") or {}).get("latency_ms"),
        "hit_at_k": bool(found) if relevant else None,
        "recall_at_k": len(found) / len(relevant) if relevant else None,
        "reciprocal_rank_at_k": 1 / first
        if first is not None
        else 0
        if relevant
        else None,
        "unanswerable_returned_candidates": bool(items) if not relevant else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row for row in rows if row["answerable"]]
    completed = [row for row in answerable if row["status"] == "completed"]
    count = len(answerable)
    return {
        "query_count": len(rows),
        "unavailable_count": sum(row["status"] != "completed" for row in rows),
        "answerable_count": count,
        "completed_answerable_count": len(completed),
        "hit_rate_at_k_all_answerable": sum(
            bool(row.get("hit_at_k")) for row in answerable
        )
        / count
        if count
        else None,
        "mean_recall_at_k_all_answerable": sum(
            row.get("recall_at_k") or 0 for row in answerable
        )
        / count
        if count
        else None,
        "mrr_at_k_all_answerable": sum(
            row.get("reciprocal_rank_at_k") or 0 for row in answerable
        )
        / count
        if count
        else None,
        "unanswerable_candidate_count": sum(
            row.get("unanswerable_returned_candidates") is True for row in rows
        ),
        "answer_generation_performed": False,
        "outcome_utility_measured": False,
    }


def run_suite(
    path: Path,
    *,
    command: list[str],
    goal_id: str,
    agent_id: str,
    registry: str | None = None,
    runtime_root: str | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ValueError("loopx command must be a nonempty argv list")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout must be positive and finite")
    suite_bytes = path.read_bytes()
    suite, hashes, corpus_digest = load_suite(path)
    limit = suite.get("max_results", 3)
    profiles = {
        row["id"]: resolve_path(path.parent, row["profile"])
        for row in suite["providers"]
    }
    profile_digests = {
        key: hashlib.sha256(profile.read_bytes()).hexdigest()
        for key, profile in profiles.items()
    }
    prefix = list(command)
    if registry:
        prefix += ["--registry", registry]
    if runtime_root:
        prefix += ["--runtime-root", runtime_root]
    reports: dict[str, list[dict[str, Any]]] = {
        provider["id"]: [] for provider in suite["providers"]
    }
    for query in suite["queries"]:
        for provider in suite["providers"]:
            argv = prefix + [
                "--format",
                "json",
                "decision-context",
                "recall-context",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
                "--profile",
                str(profiles[provider["id"]]),
                "--context-scope-ref",
                provider["scope_ref"],
                "--query",
                query["query"],
                "--query-summary",
                "Frozen context-provider comparison",
                "--max-results",
                str(limit),
            ]
            started = time.monotonic()
            row: dict[str, Any] = {
                "query_id": query["id"],
                "answerable": bool(query["relevant_ids"]),
            }
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=timeout_seconds,
                    check=False,
                )
                if result.returncode:
                    row.update(status="unavailable", reason_code="cli_failed")
                else:
                    row.update(
                        assess(
                            json.loads(result.stdout),
                            hashes,
                            query["relevant_ids"],
                            limit,
                        )
                    )
            except subprocess.TimeoutExpired:
                row.update(status="unavailable", reason_code="cli_timeout")
            except (ValueError, KeyError, TypeError):
                row.update(status="unavailable", reason_code="native_contract_invalid")
            except OSError:
                row.update(status="unavailable", reason_code="cli_unavailable")
            row["native_cli_wall_ms"] = round((time.monotonic() - started) * 1000, 3)
            reports[provider["id"]].append(row)
    unchanged = all(
        hashlib.sha256(profiles[key].read_bytes()).hexdigest() == digest
        for key, digest in profile_digests.items()
    )
    snapshot_unchanged = (
        path.read_bytes() == suite_bytes
        and hashlib.sha256(
            resolve_path(path.parent, suite["corpus"]).read_bytes()
        ).hexdigest()
        == corpus_digest
    )
    complete = unchanged and snapshot_unchanged and all(
        row["status"] == "completed" for rows in reports.values() for row in rows
    )
    return {
        "ok": complete,
        "status": "completed" if complete else "incomplete",
        "schema_version": "context_provider_comparison_report_v0",
        "visibility": "local_private",
        "corpus_sha256": corpus_digest,
        "suite_sha256": hashlib.sha256(suite_bytes).hexdigest(),
        "document_count": len(hashes),
        "max_results": limit,
        "profile_bindings_unchanged": unchanged,
        "input_snapshot_unchanged": snapshot_unchanged,
        "comparison_inputs_qualified": unchanged and snapshot_unchanged,
        "order": "query-major, declared provider order; no cache/run-order control",
        "relevance": "exact returned-content hash against frozen corpus; missing/unavailable queries stay in denominator",
        "scores_compared_across_providers": False,
        "raw_queries_or_content_persisted": False,
        "providers": {
            key: {"rows": rows, "summary": summarize(rows)}
            for key, rows in reports.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--loopx-command-json", default='["loopx"]')
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--runtime-root")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args()
    report = run_suite(
        args.suite,
        command=json.loads(args.loopx_command_json),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        registry=args.registry,
        runtime_root=args.runtime_root,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
