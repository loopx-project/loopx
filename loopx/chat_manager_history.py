"""Recent Core delivery receipts, filtered before presentation limits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .chat_manager_details import _text
from .control_plane.runtime.run_context_retention import goal_semantic_history_from_runs
from .control_plane.work_items.delivery_outcome import PROGRESS_DELIVERY_OUTCOMES
from .history import STATUS_NEUTRAL_CLASSIFICATIONS, load_index


def _recorded_details(run):
    """Hydrate existing Core fields; never dereference artifact paths or URLs."""
    observation = run.get("progress_observation")
    checkpoint = run.get("vision_checkpoint")
    vision = run.get("agent_vision")
    observation = observation if isinstance(observation, dict) else {}
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    vision = vision if isinstance(vision, dict) else {}
    delta = vision.get("path_delta")
    delta = delta if isinstance(delta, dict) else {}
    fields = {
        "checkpoint_reason": (checkpoint.get("unchanged_reason"), 600),
        "observed_reality": (delta.get("observed_reality"), 420),
        "path_outcome": (delta.get("outcome"), 40),
        "result_class": (observation.get("result_class"), 80),
        "probe_kind": (observation.get("probe_kind"), 160),
        "surface_id": (observation.get("surface_id"), 160),
    }
    values = {
        key: _text(value, limit)
        for key, (value, limit) in fields.items()
        if isinstance(value, str) and value.strip()
    }
    evidence = observation.get("evidence_ids")
    valid_evidence = isinstance(evidence, list) and all(
        isinstance(item, str) and item.strip() for item in evidence
    )
    # Stable references preserve lineage without exposing file locations or
    # treating arbitrary evidence identifiers as fetch instructions.
    refs = [
        "sha256:" + hashlib.sha256(item.encode()).hexdigest()
        for item in evidence[:8]
    ] if valid_evidence else []
    return {
        "source": "core_run_index",
        "verification": "recorded_claim_not_independent_verification",
        "artifact_read_status": "not_read",
        **values,
        "field_coverage": {
            "missing": [key for key in fields if key not in values],
            "truncated": [
                key for key, (value, limit) in fields.items()
                if isinstance(value, str) and len(value.strip()) > limit
            ],
            "invalid": [
                key for key, (value, _) in fields.items()
                if value is not None and not isinstance(value, str)
            ],
        },
        "evidence_refs": refs,
        "evidence_coverage": {
            "status": "read" if valid_evidence else "missing_or_invalid",
            "known": len(evidence) if valid_evidence else None,
            "included": len(refs),
            "omitted": max(0, len(evidence) - len(refs)) if valid_evidence else None,
        },
    }


def read_manager_delivery_history(
    runtime_root: Path, goal_id: str, *, now=None, limit=24, offset=None, lookback_days=1,
    total_limit=None,
):
    now = now or datetime.now().astimezone()
    if type(lookback_days) is not int or not 1 <= lookback_days <= 90:
        raise ValueError("lookback_days must be 1..90")
    if total_limit is not None and (
        type(total_limit) is not int or not 1 <= total_limit <= 500
    ):
        raise ValueError("total_limit must be 1..500")
    start = (now - timedelta(days=lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    base = {
        "window_start": start.isoformat(),
        "window_end": now.isoformat(),
        "window_days": lookback_days,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "deliveries": [],
    }
    try:
        if not path.is_file():
            raise OSError("missing Core run index")
        before = path.stat()
        runs, raw_count = load_index(path)
        after = path.stat()

        def signature(st):
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

        if signature(before) != signature(after):
            return {
                **base,
                "status": "conflicting",
                "coverage": {"matched": None, "included": 0, "omitted": None},
            }

        rows = []
        invalid = 0
        for run in runs:
            if run.get("classification") in STATUS_NEUTRAL_CLASSIFICATIONS:
                continue
            if run.get("delivery_outcome") not in PROGRESS_DELIVERY_OUTCOMES:
                continue
            try:
                at = datetime.fromisoformat(
                    str(run.get("generated_at")).replace("Z", "+00:00")
                )
                if at.tzinfo is None:
                    raise ValueError("timestamp has no offset")
            except ValueError:
                invalid += 1
                continue
            if not start <= at <= now:
                continue
            if run.get("goal_id") not in (None, goal_id):
                invalid += 1
                continue
            semantic = goal_semantic_history_from_runs([run])
            evidence = any(
                a.get("latest_evidence_delivery_run") for a in semantic["agents"]
            )
            details = _recorded_details(run)
            rows.append(
                {
                    "recorded_at": at.isoformat(),
                    "goal_id": goal_id,
                    "agent_id": _text(run.get("agent_id"), 160),
                    "todo_id": _text(run.get("todo_id"), 160),
                    "classification": _text(run.get("classification"), 200),
                    "reported_follow_up": _text(run.get("recommended_action"), 360),
                    "outcome": run["delivery_outcome"],
                    "verification": "core_recorded_evidence_refs"
                    if evidence
                    else "agent_reported_outcome",
                    "recorded_details": details,
                    "evidence_count": details["evidence_coverage"]["known"],
                    "source_ref": "sha256:"
                    + hashlib.sha256(
                        json.dumps(run, sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
        rows.sort(key=lambda r: datetime.fromisoformat(r["recorded_at"]), reverse=True)
        included = []
        day_counts = {}
        for row in rows:
            day = (
                datetime.fromisoformat(row["recorded_at"])
                .astimezone(now.tzinfo)
                .date()
                .isoformat()
            )
            day_counts[day] = day_counts.get(day, 0) + 1
            if day_counts[day] <= limit:
                included.append(row)
        if offset is not None:
            included = rows[offset:offset + limit]
        elif total_limit is not None and len(included) > total_limit:
            # A wider window must not grow the model context without a bound.
            included = included[:total_limit]
        return {
            **base,
            "status": "read",
            "source_revision": "sha256:"
            + hashlib.sha256(str(signature(after)).encode()).hexdigest(),
            "deliveries": included,
            "coverage": {
                "index_records": raw_count,
                "matched": len(rows),
                "included": len(included),
                "omitted": len(rows) - len(included),
                "matched_by_day": day_counts,
                "limit_per_day": limit if offset is None else None,
                "total_limit": total_limit if offset is None else None,
                **({"offset": offset} if offset is not None else {}),
                "invalid_delivery_records": invalid,
            },
            "limitations": [
                "Recorded time is not necessarily work time. Agent-reported outcomes are not independent verification.",
                "Evidence references identify receipts; referenced artifacts have not been read.",
            ],
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {
            **base,
            "status": "unavailable",
            "coverage": {"matched": None, "included": 0, "omitted": None},
        }
