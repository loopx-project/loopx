"""Typed progress-review receipts stored in goal runtime state.

An optional observer writes one receipt per captured work transition after it
has evaluated the scoped file delta outside every core transaction. The core
reads receipts only through :func:`normalize_progress_review_receipt`; prose,
raw deltas, model transcripts and credentials never enter this contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from ...control_plane.content_digest import BARE_SHA256_PATTERN

PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION = "progress_review_receipt_v0"
PROGRESS_REVIEW_RECEIPT_STATUSES: tuple[str, ...] = (
    "completed",
    "abstained",
    "failed",
    "not_evaluated",
    "stale",
)
PROGRESS_REVIEW_CHOICE_QUESTIONS: dict[str, tuple[str, ...]] = {
    "relation": ("on_goal", "necessary_prerequisite", "off_goal", "unknown"),
    "increment": ("new_evidence", "no_new_evidence", "unknown"),
}
PROGRESS_REVIEW_NOUL_QUESTIONS: tuple[str, ...] = (
    "behavior_change",
    "serves_acceptance",
    "evidence_increment",
)
PROGRESS_REVIEW_SIGNAL_KEYS: tuple[str, ...] = ("noul", "choice")
PROGRESS_REVIEW_SIGNAL_RULE_VERSION = "progress_review_signal_rule_v1"
PROGRESS_REVIEW_PENDING_REASON = "pending_evaluation"
MAX_RECEIPT_BYTES = 65536
MAX_LOADED_RECEIPTS = 256
_TEXT_LIMIT = 200


def progress_review_receipt_root(runtime_root: Path, goal_id: str) -> Path:
    # Imported here so this contract module stays free of the runtime/history
    # import chain and can be loaded by configuration surfaces at start-up.
    from ...runtime import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    return (
        runtime_root.expanduser() / "goals" / safe_goal_id / "progress-review" / "receipts"
    )


def _text(value: Any, *, field: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"receipt.{field} is required")
        return None
    if not isinstance(value, str):
        raise TypeError(f"receipt.{field} must be a string")
    text = value.strip()
    if required and not text:
        raise ValueError(f"receipt.{field} is required")
    if len(text) > _TEXT_LIMIT or any(ord(char) < 32 for char in text):
        raise ValueError(f"receipt.{field} is not a bounded identifier")
    return text or None


def _hex64(value: Any, *, field: str) -> str:
    text = _text(value, field=field)
    if text is None or not BARE_SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"receipt.{field} must be a sha256 hex digest")
    return text


def _probability(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"receipt.{field} must be a probability or null")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"receipt.{field} must be within [0, 1]")
    return number


def _optional_bool(value: Any, *, field: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"receipt.{field} must be a boolean or null")


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"receipt.{field} must be a non-negative integer")
    return int(value)


def noul_drift_signal(
    serves_acceptance: float | None,
    evidence_increment: float | None,
    minimum: float,
) -> bool | None:
    """Drift when the delta neither serves acceptance nor adds goal evidence.

    Whether the delta changes runtime behaviour is recorded but not gating: a
    behaviour change that serves nothing is still drift, and documentation or a
    negative finding that serves acceptance or adds evidence is not.
    """

    if serves_acceptance is None or evidence_increment is None:
        return None
    ceiling = 1.0 - minimum
    if serves_acceptance <= ceiling and evidence_increment <= ceiling:
        return True
    if serves_acceptance >= minimum or evidence_increment >= minimum:
        return False
    return None


def choice_drift_signal(relation: str | None, increment: str | None) -> bool | None:
    if relation == "off_goal" and increment == "no_new_evidence":
        return True
    if relation in {"on_goal", "necessary_prerequisite"} or increment == "new_evidence":
        return False
    return None


def derive_drift_signals(
    judgments: Mapping[str, Any],
    *,
    threshold: float,
    status: str,
) -> dict[str, bool | None]:
    """The only place the receipt boolean signals are defined."""

    if status != "completed":
        return {"noul": None, "choice": None}
    noul = judgments.get("noul")
    choice = judgments.get("choice")
    return {
        "noul": (
            noul_drift_signal(
                noul.get("serves_acceptance"), noul.get("evidence_increment"), threshold
            )
            if isinstance(noul, Mapping)
            else None
        ),
        "choice": (
            choice_drift_signal(choice.get("relation"), choice.get("increment"))
            if isinstance(choice, Mapping)
            else None
        ),
    }


def normalize_progress_review_receipt(value: Any) -> dict[str, Any]:
    """Validate one receipt; every field is typed and bounded.

    The drift booleans are recomputed from the typed judgments with the
    receipt's own threshold; a receipt whose booleans disagree with its
    judgments is rejected, so a writer cannot assert drift without evidence.
    """

    if not isinstance(value, Mapping):
        raise TypeError("receipt must be an object")
    if value.get("schema_version") != PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"receipt must use {PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION}"
        )
    if value.get("signal_rule_version") != PROGRESS_REVIEW_SIGNAL_RULE_VERSION:
        raise ValueError(
            f"receipt must use {PROGRESS_REVIEW_SIGNAL_RULE_VERSION}"
        )
    status = _text(value.get("status"), field="status")
    reason = _text(value.get("reason"), field="reason", required=False)
    if reason is not None and not re.fullmatch(r"[a-z0-9_]{1,80}", reason):
        raise ValueError("receipt.reason must be a bounded lowercase token")
    if status not in PROGRESS_REVIEW_RECEIPT_STATUSES:
        raise ValueError("receipt.status is not a known status")
    raw_run = value.get("run")
    if not isinstance(raw_run, Mapping):
        raise TypeError("receipt.run must be an object")
    run = {
        "turn_instance_id": _text(
            raw_run.get("turn_instance_id"), field="run.turn_instance_id", required=False
        ),
        "generated_at": _text(raw_run.get("generated_at"), field="run.generated_at"),
        "agent_id": _text(raw_run.get("agent_id"), field="run.agent_id", required=False),
        "todo_id": _text(raw_run.get("todo_id"), field="run.todo_id", required=False),
    }
    raw_judgments = value.get("judgments")
    if not isinstance(raw_judgments, Mapping):
        raise TypeError("receipt.judgments must be an object")
    choice_raw = raw_judgments.get("choice")
    choice: dict[str, str | None] | None = None
    if choice_raw is not None:
        if not isinstance(choice_raw, Mapping) or set(choice_raw) != set(
            PROGRESS_REVIEW_CHOICE_QUESTIONS
        ):
            raise ValueError("receipt.judgments.choice has an unexpected shape")
        choice = {}
        for name, labels in PROGRESS_REVIEW_CHOICE_QUESTIONS.items():
            label = choice_raw.get(name)
            if label is not None and label not in labels:
                raise ValueError(f"receipt.judgments.choice.{name} is not a label")
            choice[name] = label
    noul_raw = raw_judgments.get("noul")
    noul: dict[str, float | None] | None = None
    if noul_raw is not None:
        if not isinstance(noul_raw, Mapping) or set(noul_raw) != set(
            PROGRESS_REVIEW_NOUL_QUESTIONS
        ):
            raise ValueError("receipt.judgments.noul has an unexpected shape")
        noul = {
            name: _probability(noul_raw.get(name), field=f"judgments.noul.{name}")
            for name in PROGRESS_REVIEW_NOUL_QUESTIONS
        }
    raw_signal = value.get("drift_signal")
    if not isinstance(raw_signal, Mapping) or set(raw_signal) != set(
        PROGRESS_REVIEW_SIGNAL_KEYS
    ):
        raise ValueError("receipt.drift_signal must name exactly noul and choice")
    drift_signal = {
        key: _optional_bool(raw_signal.get(key), field=f"drift_signal.{key}")
        for key in PROGRESS_REVIEW_SIGNAL_KEYS
    }
    raw_timing = value.get("timing_ns")
    timing: dict[str, int] = {}
    if raw_timing is not None:
        if not isinstance(raw_timing, Mapping) or len(raw_timing) > 16:
            raise TypeError("receipt.timing_ns must be a small object")
        timing = {
            str(key): _non_negative_int(item, field=f"timing_ns.{key}")
            for key, item in raw_timing.items()
        }
    raw_usage = value.get("usage")
    usage: dict[str, int] | None = None
    if raw_usage is not None:
        if not isinstance(raw_usage, Mapping) or set(raw_usage) - {
            "input_tokens",
            "output_tokens",
        }:
            raise TypeError("receipt.usage may only carry token counts")
        usage = {
            str(key): _non_negative_int(item, field=f"usage.{key}")
            for key, item in raw_usage.items()
        }
    threshold = _probability(
        value.get("label_probability_threshold"), field="label_probability_threshold"
    )
    if threshold is None or threshold < 0.5:
        raise ValueError("receipt.label_probability_threshold must be at least 0.5")
    expected_signal = derive_drift_signals(
        {"choice": choice, "noul": noul}, threshold=threshold, status=status
    )
    if drift_signal != expected_signal:
        raise ValueError("receipt.drift_signal disagrees with its typed judgments")
    recorded_at = value.get("recorded_at")
    if (
        isinstance(recorded_at, bool)
        or not isinstance(recorded_at, (int, float))
        or not math.isfinite(float(recorded_at))
        or float(recorded_at) < 0
    ):
        raise TypeError("receipt.recorded_at must be a non-negative epoch number")
    event_id = _hex64(value.get("event_id"), field="event_id")
    return {
        "schema_version": PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION,
        "receipt_id": event_id,
        "goal_id": _text(value.get("goal_id"), field="goal_id"),
        "event_id": event_id,
        "evidence_id": _hex64(value.get("evidence_id"), field="evidence_id"),
        "contract_revision": _hex64(
            value.get("contract_revision"), field="contract_revision"
        ),
        "sequence": _non_negative_int(value.get("sequence"), field="sequence"),
        "run": run,
        "status": status,
        "reason": reason,
        "signal_rule_version": PROGRESS_REVIEW_SIGNAL_RULE_VERSION,
        "question_version": _text(value.get("question_version"), field="question_version"),
        "model": _text(value.get("model"), field="model"),
        "judgments": {"choice": choice, "noul": noul},
        "drift_signal": drift_signal,
        "label_probability_threshold": threshold,
        "timing_ns": timing,
        "usage": usage,
        "recorded_at": float(recorded_at),
        "authority": "none",
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError("refusing to replace a symlink receipt path")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2
    ).encode("utf-8")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds the byte budget")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(raw)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_progress_review_receipt(
    runtime_root: Path,
    goal_id: str,
    receipt: Mapping[str, Any],
) -> Path:
    normalized = normalize_progress_review_receipt(receipt)
    if normalized["goal_id"] != goal_id.strip():
        raise ValueError("receipt goal does not match the target goal")
    path = progress_review_receipt_root(runtime_root, goal_id) / (
        f"{normalized['event_id']}.json"
    )
    _atomic_write_json(path, normalized)
    return path


def progress_review_receipt_order_key(receipt: Mapping[str, Any]) -> tuple[str, float, int]:
    """Newest-first ordering that is valid across observer states.

    A receipt's `sequence` is the writing observer's local counter: a new
    observer state (a re-run `drift init`, a new basis revision) starts at
    zero, so sequences from different observers are not comparable. The run's
    own `generated_at` orders transitions; `recorded_at` orders late
    evaluations of one transition; `sequence` only breaks the remaining ties.
    """

    run = receipt.get("run")
    generated_at = str(run.get("generated_at") or "") if isinstance(run, Mapping) else ""
    recorded = receipt.get("recorded_at")
    recorded_at = (
        float(recorded)
        if isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
        else 0.0
    )
    return (generated_at, recorded_at, int(receipt.get("sequence") or 0))


def load_progress_review_receipts(
    runtime_root: Path,
    goal_id: str,
    *,
    limit: int = MAX_LOADED_RECEIPTS,
) -> tuple[list[dict[str, Any]], int]:
    """Return newest-first valid receipts plus the number of rejected files.

    Newest is decided by `progress_review_receipt_order_key`, so the load limit
    keeps the latest transitions even when an older observer state wrote
    higher local sequence numbers.
    """

    root = progress_review_receipt_root(runtime_root, goal_id)
    if not root.is_dir():
        return [], 0
    receipts: list[dict[str, Any]] = []
    rejected = 0
    for path in sorted(root.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            rejected += 1
            continue
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_RECEIPT_BYTES + 1)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise ValueError("oversized receipt")
            normalized = normalize_progress_review_receipt(json.loads(raw))
            if normalized["goal_id"] != goal_id.strip() or path.stem != normalized[
                "event_id"
            ]:
                raise ValueError("receipt identity does not match its path")
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            rejected += 1
            continue
        receipts.append(normalized)
    receipts.sort(key=progress_review_receipt_order_key, reverse=True)
    return receipts[: max(1, int(limit))], rejected


def progress_review_receipt_summary(
    receipts: Iterable[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    rejected: int = 0,
    stale: int = 0,
    newest_contract_revision: str | None = None,
) -> dict[str, Any]:
    """Compact, prose-free projection for status surfaces."""

    counts: dict[str, int] = {}
    latest: dict[str, Any] | None = None
    drift_counts = {key: 0 for key in PROGRESS_REVIEW_SIGNAL_KEYS}
    total = 0
    pending = 0
    for receipt in receipts:
        total += 1
        counts[receipt["status"]] = counts.get(receipt["status"], 0) + 1
        if receipt.get("reason") == PROGRESS_REVIEW_PENDING_REASON:
            pending += 1
        for key in PROGRESS_REVIEW_SIGNAL_KEYS:
            if receipt["drift_signal"].get(key) is True:
                drift_counts[key] += 1
        if latest is None:
            latest = {
                "event_id": receipt["event_id"],
                "evidence_id": receipt["evidence_id"],
                "status": receipt["status"],
                "run": dict(receipt["run"]),
                "judgments": receipt["judgments"],
                "drift_signal": dict(receipt["drift_signal"]),
                "model": receipt["model"],
                "question_version": receipt["question_version"],
            }
    summary: dict[str, Any] = {
        "schema_version": "progress_review_status_v0",
        "mode": policy.get("mode"),
        "signal": policy.get("signal"),
        "drift_threshold": policy.get("drift_threshold"),
        "contract_revision": policy.get("contract_revision"),
        "receipt_count": total,
        "pending_receipts": pending,
        "stale_receipts": stale,
        "rejected_receipts": rejected,
        "status_counts": counts,
        "drift_counts": drift_counts,
        "latest": latest,
        "authority": "none",
    }
    if policy.get("mode") == "assist" and not policy.get("contract_revision"):
        # assist may only raise an obligation for receipts bound to a pinned
        # goal contract; without the pin the receipts stay observations.
        summary["assist_blocked_reason"] = "contract_revision_unpinned"
    pinned = policy.get("contract_revision")
    if newest_contract_revision is not None:
        summary["newest_receipt_contract_revision"] = newest_contract_revision
    if pinned and newest_contract_revision and newest_contract_revision != pinned:
        # The pin does not follow the observer basis; the receipt for the newest
        # transition is bound elsewhere, so no current evidence will accrue
        # until re-pinned. "Newest" follows the run order, never the observer's
        # local sequence.
        summary["rebind_hint"] = "newer_receipts_under_unpinned_revision"
    return summary


__all__ = [
    "MAX_LOADED_RECEIPTS",
    "MAX_RECEIPT_BYTES",
    "PROGRESS_REVIEW_PENDING_REASON",
    "PROGRESS_REVIEW_SIGNAL_RULE_VERSION",
    "choice_drift_signal",
    "derive_drift_signals",
    "noul_drift_signal",
    "PROGRESS_REVIEW_CHOICE_QUESTIONS",
    "PROGRESS_REVIEW_NOUL_QUESTIONS",
    "PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION",
    "PROGRESS_REVIEW_RECEIPT_STATUSES",
    "progress_review_receipt_order_key",
    "PROGRESS_REVIEW_SIGNAL_KEYS",
    "load_progress_review_receipts",
    "normalize_progress_review_receipt",
    "progress_review_receipt_root",
    "progress_review_receipt_summary",
    "write_progress_review_receipt",
]
