"""Synthetic evidence and independent acceptance, with no provider dependency."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

WORKERS = ("analyst", "reviewer")
REVISIONS = ("initial", "corrected")


def roster(topology: str, team_size: tuple[int, int, int] | None = None) -> list[dict]:
    if team_size is not None:
        if topology != "mixed" or len(team_size) != 3 or any(type(n) is not int or n < 1 for n in team_size):
            raise ValueError("mixed_team_requires_positive_luna_dsh_ark_counts")
        # Each layer adopts an exact predecessor. The lead must also consume every
        # member, including extra analysts with no downstream reviewer assigned.
        members = []
        previous = []
        for (host, revision), count in zip((("codex", "initial"), ("dsh", "corrected"), ("ark", "corrected")), team_size):
            current = []
            for index in range(count):
                worker = f"{host}-{index + 1}"
                row = {"worker": worker, "revision": revision, "host": host}
                if previous:
                    row["upstream"] = previous[index % len(previous)]
                members.append(row)
                current.append(worker + "/" + revision)
            previous = current
        return members
    if topology == "local-led":
        members = [{"worker": worker, "revision": revision, "host": host} for worker, revision, host in (
            ("local-analyst", "initial", "dsh"), ("cloud-reviewer", "initial", "ark"),
            ("local-reviewer", "corrected", "dsh"), ("cloud-analyst", "corrected", "ark"),
        )]
        members[1]["upstream"] = "local-analyst/initial"
        members[2]["requester"] = "cloud-analyst"
        members[3]["upstream"] = "local-reviewer/corrected"
        return members
    if topology == "cloud-led":
        return [{"worker": worker, "revision": revision, "host": "dsh"}
                for worker in WORKERS for revision in REVISIONS]
    raise ValueError("unknown_team_topology")


def assignments(root: Path) -> list[dict]:
    path = root / "project" / "team.json"
    return json.loads(path.read_text()) if path.exists() else roster("cloud-led")


def upstream(root: Path, worker: str, revision: str) -> str | None:
    return next((row.get("upstream") for row in assignments(root)
                 if row["worker"] == worker and row["revision"] == revision), None)


class EvidenceRejected(ValueError):
    """A public-safe oracle reason, never raw model or filesystem content."""


def evidence(revision: str) -> dict:
    if revision not in REVISIONS:
        raise ValueError("unknown_revision")
    corrected = revision == "corrected"
    return {
        "synthetic": True, "revision": revision, "units": "fictional millions",
        "issuer": {"source_id": "filing-correction" if corrected else "filing-initial",
                   "family": "issuer", "period": "2026-H1", "cash_from_operations": 105 if corrected else 120,
                   "capital_expenditure": 30, "receivables_sold": 50,
                   "supersedes": "filing-initial" if corrected else None},
        "prior": {"source_id": "prior-filing", "period": "2025-FY",
                  "cash_from_operations": 90, "capital_expenditure": 25},
        "repost": {"source_id": "repost", "family": "issuer", "original": "filing-initial",
                   "cash_from_operations": 120, "capital_expenditure": 30,
                   "capture_note": "Captured after the correction; capture time is not publication time."},
        "rules": "Raw FCF = cash from operations - capital expenditure. Normalized FCF also excludes "
                 "receivables sold. Different fiscal periods cannot establish growth. Reposts of the "
                 "same original are not independent sources. A repost is stale only if superseded by "
                 "a correction with different figures, not merely because it is old.",
    }


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def task(revision: str, question: str) -> str:
    return (
        f"Read input.json, a synthetic {revision} filing. {question}\n"
        "Read DELEGATION.json and use read_context / assess_request to adopt or reject the specific request. "
        "Calculate and verify locally. Write output.json with keys input_sha256 (actual input file hash), "
        "revision, raw_fcf, normalized_fcf, period_comparable (boolean), growth_supported (boolean), "
        "independent_source_families (integer), repost_stale (boolean), source_refs (list including ALL three "
        "source_id values from issuer, prior and repost; cite the prior filing for the period-comparability check), "
        "reason (short). Count independent_source_families only for corroboration of CURRENT-period "
        "figures; prior-period comparison material is not current-period corroboration. "
        "If read_input returns an upstream artifact, independently check it and include adopted_dependencies "
        "in output.json: an object mapping upstream.identity to its full upstream.artifact_sha256. "
        "Matching its numbers or mentioning a hash in prose does not record adoption. "
        "Do not use network, read another worker, modify Goal state, commit, or trade. "
        "Write only output.json. Return the normal Turn candidate after writing the artifact."
    )


def validate_worker(workspace: Path, revision: str) -> dict:
    raw = (workspace / "input.json").read_bytes()
    if raw != encoded(evidence(revision)):
        raise EvidenceRejected("input_was_modified")
    result = json.loads((workspace / "output.json").read_text())
    if not isinstance(result, dict):
        raise EvidenceRejected("worker_output_must_be_object")
    # Independent expected semantics, not computed from the worker's answer.
    expected = {"revision": revision, "input_sha256": sha256(raw).hexdigest(),
                "raw_fcf": 75 if revision == "corrected" else 90,
                "normalized_fcf": 25 if revision == "corrected" else 40,
                "period_comparable": False, "growth_supported": False,
                "independent_source_families": 1, "repost_stale": revision == "corrected"}
    mismatches = [k for k, v in expected.items() if type(result.get(k)) is not type(v) or result[k] != v]
    if mismatches:
        raise EvidenceRejected("worker_evidence_rejected:" + ",".join(mismatches))
    required = {"filing-correction" if revision == "corrected" else "filing-initial", "prior-filing", "repost"}
    refs = result.get("source_refs")
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise EvidenceRejected("worker_source_refs_must_be_strings")
    missing_refs = required - set(refs)
    if missing_refs:
        raise EvidenceRejected("worker_source_refs_missing:" + ",".join(sorted(missing_refs)))
    if not result.get("reason"):
        raise EvidenceRejected("worker_source_explanation_missing")
    root = workspace.parent.parent
    dependency = upstream(root, workspace.parent.name, revision)
    if dependency:
        previous = json.loads((root / dependency / "output.json").read_text())
        if result.get("adopted_dependencies", {}).get(dependency) != sha256(encoded(previous)).hexdigest():
            raise EvidenceRejected("worker_did_not_adopt_upstream")
    return result


def validate_report(root: Path) -> dict:
    report = json.loads((root / "lead" / "report.json").read_text())
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), dict):
        raise EvidenceRejected("report_and_dependencies_must_be_objects")
    expected = {"initial_normalized_fcf": 40, "corrected_normalized_fcf": 25,
                "revision_delta": -15, "growth_supported": False,
                "independent_source_families": 1, "repost_stale_after_correction": True}
    if any(type(report.get(k)) is not type(v) or report[k] != v for k, v in expected.items()):
        raise ValueError("lead_conclusions_rejected")
    dependencies = report.get("dependencies", {})
    for assignment in assignments(root):
        worker, revision = assignment["worker"], assignment["revision"]
        identity = worker + "/" + revision
        output = validate_worker(root / worker / revision, revision)
        if dependencies.get(identity) != sha256(encoded(output)).hexdigest():
            raise ValueError("lead_did_not_adopt_dependency:" + identity)
    if not report.get("reason"):
        raise ValueError("lead_explanation_missing")
    return report
