"""D1 shadow lifecycle. Private evidence and model results never enter authority."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Any, Callable

import re

from loopx.capabilities.progress_review.receipt import (
    PROGRESS_REVIEW_PENDING_REASON,
    PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION,
    PROGRESS_REVIEW_SIGNAL_RULE_VERSION,
    write_progress_review_receipt,
)
from loopx.file_lock import exclusive_file_lock

from .config import Config, load_config, read_json
from .drift_capture import delta, digest, stable_capture, validate_paths
from .progress import QUESTION_VERSION
from .runner import SECRET, assess_one, read_basis
from .store import RunStore, atomic_json, initialize_run

SCHEMA = "jev_drift_shadow_v0"
LABEL_SCHEMA = "jev_drift_label_v0"
LABELS = ("drift", "on_goal", "unknown")
MAX_EVENTS = 256
MAX_PENDING = 16
_EVENT_ID = re.compile(r"^[a-f0-9]{64}$")


def policy(path: Path | None) -> Config:
    config = load_config(path)
    if config.mode == "assist":
        raise ValueError("drift_supports_off_or_shadow_only")
    if config.mode != "off" and "progress_review" not in config.scenarios:
        raise ValueError("enable_progress_review_scenario")
    return config


def state(root: Path) -> dict[str, Any]:
    value, _ = read_json(root / "state.json", 4 * 1024 * 1024)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("invalid_drift_state")
    required = {
        "goal_id",
        "repo",
        "basis_path",
        "config_path",
        "paths",
        "baseline",
        "contract_revision",
        "configuration_epoch",
        "events",
        "seen_evidence",
        "capture_failures",
        "runtime_root",
    }
    if not required <= value.keys() or not isinstance(value["events"], dict):
        raise ValueError("invalid_drift_state")
    return value


def contract(path: Path, repo: Path) -> tuple[dict[str, Any], str, Callable[[], bool]]:
    basis, guard = read_basis(path, repo)
    if not isinstance(basis.get("goal_id"), str) or not basis["goal_id"].strip():
        raise ValueError("missing_goal_identity")
    _, revision = read_json(path, 32768)
    if basis.get("criterion_binding", {}).get("origin") == "goal_acceptance":
        revision = digest({"manifest": revision, "criterion_binding": basis["criterion_binding"]})
    return basis, revision, guard


def initialize(
    root: Path,
    repo: Path,
    basis_path: Path,
    config_path: Path,
    paths: list[str],
    *,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    config = policy(config_path)
    if config.mode == "off":
        return {"status": "disabled"}
    repo = repo.resolve()
    paths = validate_paths(repo, paths)
    basis, revision, guard = contract(basis_path, repo)
    baseline = stable_capture(repo, paths)
    baseline["external_evidence"] = basis.get("evidence", [])
    if SECRET.search(str(baseline)) or not guard():
        raise ValueError("unsafe_or_changed_initial_evidence")
    if root.exists():
        raise ValueError("state_exists_use_existing_state_or_new_explicit_budget")
    root.mkdir(parents=True, mode=0o700)
    (root / "jobs").mkdir(mode=0o700)
    (root / "results").mkdir(mode=0o700)
    initialize_run(root / "requests", config.max_requests_per_run)
    atomic_json(
        root / "state.json",
        {
            "schema": SCHEMA,
            "goal_id": basis["goal_id"],
            "repo": str(repo),
            "basis_path": str(basis_path.resolve()),
            "config_path": str(config_path.resolve()),
            "paths": paths,
            "baseline": baseline,
            "contract_revision": revision,
            "events": {},
            "seen_evidence": [],
            "capture_failures": 0,
            "configuration_epoch": 0,
            # When known, typed receipts are written under the LoopX goal
            # runtime so the core can consume them; the private study state
            # below never becomes authority either way.
            "runtime_root": (
                str(runtime_root.expanduser().resolve())
                if runtime_root is not None
                else None
            ),
        },
    )
    return {
        "status": "baseline_created",
        "goal_id": basis["goal_id"],
        "scope_file_count": len(paths),
        "receipts": "goal_runtime" if runtime_root is not None else "private_only",
        # Pin this in the Goal policy (`--progress-review-contract-revision`) so
        # the core only counts receipts bound to this exact basis.
        "contract_revision": revision,
        "authority": "none",
    }


def prepare(root: Path, config_path: Path) -> dict[str, Any]:
    current = state(root)
    if str(config_path.resolve()) != current["config_path"]:
        raise ValueError("configuration_path_mismatch")
    config = policy(config_path)
    if config.mode != "shadow":
        raise ValueError("shadow_disabled")
    repo = Path(current["repo"])
    basis, revision, guard = contract(Path(current["basis_path"]), repo)
    snapshot = stable_capture(repo, current["paths"])
    snapshot["external_evidence"] = basis.get("evidence", [])
    if basis["goal_id"] != current["goal_id"] or not guard():
        raise ValueError("goal_or_evidence_changed")
    if SECRET.search(str(snapshot)) or SECRET.search(str(basis)):
        raise ValueError("credential_like_evidence")
    return {
        "snapshot": snapshot,
        "basis": basis,
        "config": config,
        "contract_revision": revision,
        "config_generation": config.generation,
        "baseline_digest": digest(current["baseline"]),
        "configuration_epoch": current["configuration_epoch"],
        "evidence_guard": guard,
    }


def invalidate_baseline(root: Path) -> None:
    """Do not compare across a missed/failed capture as if it were one work round."""
    with exclusive_file_lock(root / "capture.lock"):
        current = state(root)
        current["baseline"] = None
        current["capture_failures"] += 1
        atomic_json(root / "state.json", current)


def _require_criterion_run(basis, goal_id, run, paths):
    # The core decoder owns this identity rule for both capture and receipt use.
    if basis.get("criterion_binding", {}).get("origin") == "goal_acceptance":
        from loopx.control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected
        try:
            effect_runtime_result("progress_review.evidence_scope", {
                "goal_id": goal_id, "run": run,
                "scope": {"criterion_binding": basis["criterion_binding"],
                          "coverage": "declared_file_net_change", "files": paths},
            })
        except EffectRuntimeRejected as exc:
            raise ValueError("run_acceptance_scope_mismatch") from exc


def enqueue(
    root: Path,
    prepared: dict[str, Any],
    record_path: Path,
    *,
    prepare_ns: int | None = None,
    owner_command_ns: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    record, record_digest = read_json(record_path, 4 * 1024 * 1024)
    with exclusive_file_lock(root / "capture.lock"):
        current = state(root)
        if record.get("goal_id") != current["goal_id"] or not record.get(
            "generated_at"
        ):
            raise ValueError("run_goal_or_identity_mismatch")
        _require_criterion_run(prepared["basis"], current["goal_id"], record, current["paths"])
        # Checkpoint supplements for the same bound Turn are the same transition.
        identity = (
            {
                "turn": record["turn_instance_id"],
                "goal": current["goal_id"],
                "agent": record.get("agent_id"),
                "todo": record.get("todo_id"),
            }
            if record.get("turn_instance_id")
            else {"run_digest": record_digest}
        )
        event_id = digest(identity)
        if event_id in current["events"]:
            return {"status": "duplicate_event", "event_id": event_id}
        if len(current["events"]) >= MAX_EVENTS:
            raise ValueError("event_retention_budget_exhausted")
        live = prepare(root, Path(current["config_path"]))
        if (
            live["snapshot"] != prepared["snapshot"]
            or live["contract_revision"] != prepared["contract_revision"]
            or live["config_generation"] != prepared["config_generation"]
            or live["configuration_epoch"] != prepared["configuration_epoch"]
            or not prepared["evidence_guard"]()
        ):
            raise ValueError("evidence_changed_during_refresh")
        previous = current["baseline"]
        captured = prepared["snapshot"]
        status = "queued"
        text = ""
        if (
            previous is None
            or current["contract_revision"] != prepared["contract_revision"]
            or digest(previous) != prepared["baseline_digest"]
        ):
            status = "baseline_reset"
        else:
            text = delta(previous, captured)
            if not text:
                status = (
                    "no_delta"
                    if previous["index_digest"] == captured["index_digest"]
                    else "index_only_change_unknown"
                )
        # Equal patches against different surrounding source are different evidence.
        context = {
            "before": previous["files"] if previous else None,
            "after": captured["files"],
        }
        evidence_id = digest(
            {
                "contract": prepared["contract_revision"],
                "delta": text,
                "context": context,
            }
        )
        if status == "queued" and evidence_id in current["seen_evidence"]:
            status = "duplicate_evidence"
        if status == "queued":
            pending = sum(
                row["status"] == "queued" for row in current["events"].values()
            )
            if pending >= MAX_PENDING:
                raise ValueError("pending_evidence_budget_exhausted")
            basis = dict(prepared["basis"])
            basis["evidence"] = [
                *basis.get("evidence", []),
                {
                    "ref": "scoped-checkpoint-context",
                    "text": json.dumps(context, ensure_ascii=False, sort_keys=True),
                    "origin": "host_scoped_file_read",
                    "sha256": digest(context),
                },
                {
                    "ref": "captured-workspace-delta",
                    "text": text,
                    "origin": "host_scoped_file_comparison",
                    "sha256": digest(text),
                },
            ]
            basis["horizon"] = (
                "Historical net change between two explicit checkpoints in the listed files only. "
                "Do not infer whole-task progress, tool success, or exclusive authorship. "
                "Missing surrounding context requires unknown."
            )
            snapshot = {
                "schema": "jev_progress_input_v0",
                "scenario": "progress_review",
                "source": {
                    "owner": "scoped_checkpoint_capture",
                    "revision": evidence_id,
                },
                "facts": {
                    "work_summary": "Inspect the host-captured scoped delta; no Agent self-report supplied.",
                    "history_available": True,
                },
            }
            job = {
                "event_id": event_id,
                "evidence_id": evidence_id,
                "run": {
                    key: (str(record[key]) if record.get(key) is not None else None)
                    for key in ("turn_instance_id", "generated_at", "agent_id", "todo_id")
                },
                "record_digest": record_digest,
                "contract_revision": prepared["contract_revision"],
                "config_generation": prepared["config_generation"],
                "configuration_epoch": prepared["configuration_epoch"],
                "source_record": str(record_path.resolve()),
                "basis": basis,
                "snapshot": snapshot,
                "paths": current["paths"],
            }
            atomic_json(root / "jobs" / f"{event_id}.json", job)
            current["seen_evidence"].append(evidence_id)
            if current.get("runtime_root"):
                # A pending receipt tells the core this transition is being
                # evaluated, so an existing streak is neither counted up nor
                # dissolved while the separate consumer is still running.
                _emit_receipt(
                    Path(current["runtime_root"]),
                    current["goal_id"],
                    job,
                    len(current["events"]),
                    {"status": "not_evaluated", "reason": PROGRESS_REVIEW_PENDING_REASON},
                    prepared["config"],
                    evaluation_ns=0,
                )
        current["baseline"] = captured
        current["contract_revision"] = prepared["contract_revision"]
        current["events"][event_id] = {
            "sequence": len(current["events"]),
            "status": status,
            "evidence_id": evidence_id,
            "capture_ns": time.perf_counter_ns() - started,
            "prepare_ns": prepare_ns,
            "owner_command_ns": owner_command_ns,
        }
        atomic_json(root / "state.json", current)
    return {"event_id": event_id, "status": status, "authority": "none"}


def drain(
    root: Path,
    config_path: Path | None,
    *,
    transport: Callable[..., dict[str, Any]] | None = None,
    credential: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    config = policy(config_path)
    if config.mode == "off":
        return {"status": "disabled"}
    processed = []
    # This is an extension-only consumer lock. Capture and LoopX use other locks.
    # A concurrent consumer cannot finalize an in-flight reservation as a failure.
    with exclusive_file_lock(root / "consumer.lock"):
        initial = state(root)
        if config_path is None or str(config_path.resolve()) != initial["config_path"]:
            raise ValueError("configuration_path_mismatch")
        for event_id, row in sorted(
            initial["events"].items(), key=lambda item: item[1]["sequence"]
        ):
            if row["status"] != "queued":
                continue
            job_path = root / "jobs" / f"{event_id}.json"
            job, job_digest = read_json(job_path, 256 * 1024)

            def current() -> bool:
                try:
                    _require_criterion_run(job["basis"], initial["goal_id"], job["run"], job["paths"])
                    now = policy(config_path)
                    # Legacy studies keep their manifest-only guard. New canonical
                    # scopes must recheck the same complete owner-bound revision.
                    if job.get("basis", {}).get("criterion_binding", {}).get("origin") == "goal_acceptance":
                        _, revision, basis_current = contract(Path(initial["basis_path"]), Path(initial["repo"]))
                        if not basis_current():
                            return False
                    else:
                        _, revision = read_json(Path(initial["basis_path"]), 32768)
                    return (
                        now.mode == "shadow"
                        and now.generation == job["config_generation"]
                        and state(root)["configuration_epoch"]
                        == job["configuration_epoch"]
                        and revision == job["contract_revision"]
                        and read_json(job_path, 256 * 1024)[1] == job_digest
                        and read_json(Path(job["source_record"]), 4 * 1024 * 1024)[1]
                        == job["record_digest"]
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    return False

            options: dict[str, Any] = {}
            if transport is not None:
                options["transport"] = transport
            if credential is not None:
                options["credential"] = credential
            started = time.perf_counter_ns()
            result = assess_one(
                job["snapshot"],
                job["basis"],
                config,
                RunStore(root / "requests"),
                current,
                **options,
            )
            evaluation_ns = time.perf_counter_ns() - started
            receipt_record = None
            if initial.get("runtime_root"):
                receipt_record = _emit_receipt(
                    Path(initial["runtime_root"]),
                    initial["goal_id"],
                    job,
                    initial["events"][event_id]["sequence"],
                    result,
                    config,
                    evaluation_ns=evaluation_ns,
                )
            report = {
                "schema": SCHEMA,
                "event_id": event_id,
                "evidence_id": job["evidence_id"],
                "mode": "shadow",
                "authority": "none",
                "observer_influence": "none",
                "core_consumption": "goal_progress_review_policy",
                "historical_only": True,
                "assessment": result,
                "evaluation_ns": evaluation_ns,
                "receipt": receipt_record,
            }
            atomic_json(root / "results" / f"{event_id}.json", report)
            with exclusive_file_lock(root / "capture.lock"):
                latest = state(root)
                latest["events"][event_id]["status"] = result["status"]
                atomic_json(root / "state.json", latest)
            # Raw delta is no longer needed after the immutable result is saved.
            job_path.unlink()
            processed.append({"event_id": event_id, "status": result["status"]})
    return {"status": "drained", "processed": processed, "authority": "none"}


def _emit_receipt(
    runtime_root: Path,
    goal_id: str,
    job: dict[str, Any],
    sequence: int,
    result: dict[str, Any],
    config: Config,
    *,
    evaluation_ns: int,
) -> dict[str, Any]:
    """Write one typed receipt for the core; failures are recorded, never raised."""

    assessment = result.get("assessment") if isinstance(result.get("assessment"), dict) else None
    completed = result.get("status") == "completed" and assessment is not None
    reason = result.get("reason")
    reason_token = (
        reason if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,80}", reason) else None
    )
    timing: dict[str, int] = {"evaluation": int(evaluation_ns)}
    total = result.get("assessment_total_ns")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        timing["assessment_total"] = total
    worker = result.get("worker_timing_ns")
    if isinstance(worker, dict):
        headers = worker.get("request_to_headers")
        if isinstance(headers, int) and not isinstance(headers, bool) and headers >= 0:
            timing["request_to_headers"] = headers
    receipt = {
        "schema_version": PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "event_id": job["event_id"],
        "evidence_id": job["evidence_id"],
        "contract_revision": job["contract_revision"],
        "sequence": sequence,
        "run": job.get("run") or {},
        "status": result.get("status"),
        "reason": reason_token,
        "signal_rule_version": PROGRESS_REVIEW_SIGNAL_RULE_VERSION,
        "question_version": QUESTION_VERSION,
        "model": config.model,
        "judgments": {
            "choice": assessment.get("judgments") if assessment else None,
            "noul": assessment.get("noul") if assessment else None,
        },
        "drift_signal": (
            dict(assessment.get("drift_signal") or {})
            if completed
            else {"noul": None, "choice": None}
        ),
        "label_probability_threshold": config.minimum_label_probability,
        "timing_ns": timing,
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else None,
        "recorded_at": time.time(),
        **({"evidence_scope": {"criterion_binding": job["basis"]["criterion_binding"],
            "coverage": "declared_file_net_change", "files": job.get("paths", [])}}
           if (job.get("basis") or {}).get("criterion_binding") else {}),
    }
    try:
        path = write_progress_review_receipt(runtime_root, goal_id, receipt)
    except (OSError, ValueError, TypeError):
        return {"status": "write_failed", "reason": "invalid_or_unwritable_receipt"}
    return {"status": "written", "path": str(path)}


def label(root: Path, event_id: str, truth: str, note: str = "") -> dict[str, Any]:
    """Record a private human truth label for one observed event."""

    if truth not in LABELS:
        raise ValueError("invalid_label")
    if not _EVENT_ID.fullmatch(str(event_id)):
        raise ValueError("invalid_event_id")
    text = str(note or "")
    if len(text) > 200 or any(ord(char) < 32 for char in text):
        raise ValueError("invalid_label_note")
    with exclusive_file_lock(root / "capture.lock"):
        current = state(root)
        if event_id not in current["events"]:
            raise ValueError("unknown_event")
        atomic_json(
            root / "results" / f"{event_id}.label.json",
            {
                "schema": LABEL_SCHEMA,
                "event_id": event_id,
                "truth": truth,
                "note": text,
                "labeled_at": time.time(),
            },
        )
    return status(root)


def _agreement(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Confusion counts of private labels against each derived drift signal."""

    table: dict[str, dict[str, int]] = {}
    for signal in ("noul", "choice"):
        cell = {
            "true_positive": 0,
            "false_positive": 0,
            "false_negative": 0,
            "true_negative": 0,
            "undecided": 0,
        }
        for row in rows:
            truth = (row.get("label") or {}).get("truth")
            if truth not in {"drift", "on_goal"}:
                continue
            predicted = (row.get("drift_signal") or {}).get(signal)
            if predicted is None:
                cell["undecided"] += 1
            elif predicted and truth == "drift":
                cell["true_positive"] += 1
            elif predicted and truth == "on_goal":
                cell["false_positive"] += 1
            elif not predicted and truth == "drift":
                cell["false_negative"] += 1
            else:
                cell["true_negative"] += 1
        table[signal] = cell
    return table


def status(root: Path) -> dict[str, Any]:
    current = state(root)
    counts: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    receipts_written = 0
    rows = []
    for event_id, item in sorted(
        current["events"].items(), key=lambda item: item[1]["sequence"]
    ):
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        row = {"event_id": event_id, **item}
        label_path = root / "results" / f"{event_id}.label.json"
        if label_path.is_file():
            recorded_label, _ = read_json(label_path, 4096)
            if isinstance(recorded_label, dict) and recorded_label.get("schema") == LABEL_SCHEMA:
                row["label"] = {
                    "truth": recorded_label.get("truth"),
                    "note": recorded_label.get("note"),
                }
                truth = str(recorded_label.get("truth"))
                label_counts[truth] = label_counts.get(truth, 0) + 1
        report_path = root / "results" / f"{event_id}.json"
        if report_path.is_file():
            report, _ = read_json(report_path)
            assessment = report["assessment"]
            receipt_record = report.get("receipt")
            if isinstance(receipt_record, dict) and receipt_record.get("status") == "written":
                receipts_written += 1
            row.update(
                judgments=assessment.get("assessment", {}).get("judgments"),
                noul=assessment.get("assessment", {}).get("noul"),
                drift_signal=assessment.get("assessment", {}).get("drift_signal"),
                receipt=receipt_record,
                reason=assessment.get("reason"),
                execution_kind=assessment.get("execution_kind"),
                assessment_total_ns=assessment.get("assessment_total_ns"),
                evaluation_ns=report["evaluation_ns"],
                request_id=assessment.get("request_id"),
                usage=assessment.get("usage"),
                assessment_timing_ns=assessment.get("assessment_timing_ns"),
                transport_timing_ns=assessment.get("transport_timing_ns"),
                worker_timing_ns=assessment.get("worker_timing_ns"),
                cached_provider_measurements=assessment.get(
                    "cached_provider_measurements", False
                ),
            )
        rows.append(row)
    configured = policy(Path(current["config_path"]))
    return {
        "schema": SCHEMA,
        "goal_id": current["goal_id"],
        "mode": configured.mode,
        "authority": "none",
        # The observer never steers. Whether the core turns these receipts into
        # an obligation is decided by the Goal's registry `progress_review` policy.
        "observer_influence": "none",
        "core_consumption": "goal_progress_review_policy",
        "historical_only": True,
        "runtime_root": current.get("runtime_root"),
        "contract_revision": current.get("contract_revision"),
        "receipts_written": receipts_written,
        "label_counts": label_counts,
        "label_agreement": _agreement(rows),
        "counts": counts,
        "capture_failures": current["capture_failures"],
        "scope_file_count": len(current["paths"]),
        "model": configured.model,
        "allow_egress": configured.allow_egress,
        "label_probability_threshold": configured.minimum_label_probability,
        "events": rows,
        "limits": {
            k: v
            for k, v in asdict(configured).items()
            if k in {"deadline_ms", "max_requests_per_run", "max_request_bytes"}
        },
    }


def configure(root: Path, mode: str) -> dict[str, Any]:
    if mode not in {"off", "shadow"}:
        raise ValueError("drift_supports_off_or_shadow_only")
    with exclusive_file_lock(root / "capture.lock"):
        current = state(root)
        config_path = Path(current["config_path"])
        configured = policy(config_path)
        if mode == "shadow" and (
            not configured.model.strip()
            or "latest" in configured.model.lower()
            or "progress_review" not in configured.scenarios
        ):
            raise ValueError("shadow_requires_pinned_model_and_progress_scenario")
        value, _ = read_json(config_path, 16384)
        value["mode"] = mode
        current["configuration_epoch"] += 1
        current["baseline"] = None
        atomic_json(root / "state.json", current)
        atomic_json(config_path, value)
    return status(root)
