"""Goal-scoped observations for exact-head merge-readiness qualification."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json
from ...runtime import validate_goal_id_path_segment


MERGE_READINESS_OBSERVATION_SCHEMA_VERSION = (
    "pull_request_merge_readiness_observation_v0"
)
MERGE_READINESS_OBSERVATION_STORE_SCHEMA_VERSION = (
    "pull_request_merge_readiness_observation_store_v0"
)
MAX_OBSERVATIONS = 200
EXACT_HEAD_PATTERN = re.compile(r"^[1-9][0-9]*@[0-9a-f]{40}$")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({_text(item) for item in value if _text(item)})


def readiness_material_state(
    *,
    repository: str,
    item: Mapping[str, Any],
    review_threads: Mapping[str, Any],
    wait_for_ci: bool,
) -> dict[str, Any]:
    """Return only facts whose change requires a new readiness decision."""

    conclusion = _mapping(item.get("review_conclusion"))
    checks = _mapping(item.get("checks"))
    counts = _mapping(checks.get("counts"))
    number = item.get("number")
    head_oid = _text(item.get("head_oid")).casefold()
    exact_head = f"{number}@{head_oid}" if isinstance(number, int) and head_oid else None
    return {
        "repository": _text(repository).casefold(),
        "exact_head": exact_head,
        "base_oid": _text(item.get("base_oid")).casefold() or None,
        "state": _text(item.get("state")).upper(),
        "is_draft": item.get("is_draft") is True,
        "merge_state": _text(item.get("merge_state")).upper(),
        "review_decision": _text(item.get("review_decision")).upper(),
        "review_conclusion": {
            "valid": conclusion.get("valid") is True,
            "status": _text(conclusion.get("status")),
            "state": _text(conclusion.get("state")).upper(),
            "verdict": _text(conclusion.get("verdict")).upper(),
            "review_commit": _text(conclusion.get("review_commit")).casefold()
            or None,
            "invalid_reasons": _string_list(conclusion.get("invalid_reasons")),
        },
        "checks": {
            "consulted": wait_for_ci,
            "total": checks.get("total") if type(checks.get("total")) is int else None,
            "counts": {
                key: int(counts.get(key) or 0)
                for key in ("success", "failure", "pending", "unknown")
            },
            "failures": _string_list(checks.get("failures")),
            "pending": _string_list(checks.get("pending")),
        },
        "review_threads": {
            "complete": review_threads.get("complete") is True,
            "total_count": (
                review_threads.get("total_count")
                if type(review_threads.get("total_count")) is int
                else None
            ),
            "unresolved_count": (
                review_threads.get("unresolved_count")
                if type(review_threads.get("unresolved_count")) is int
                else None
            ),
            "failure_code": _text(review_threads.get("failure_code")) or None,
        },
        "wait_for_ci": wait_for_ci,
    }


def readiness_material_fingerprint(material_state: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        material_state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def observation_key(repository: str, exact_head: str) -> str:
    return f"{_text(repository).casefold()}#{_text(exact_head).casefold()}"


def build_readiness_observation(
    *, goal_id: str, readiness: Mapping[str, Any]
) -> dict[str, Any]:
    material_state = _mapping(readiness.get("material_state"))
    fingerprint = _text(readiness.get("material_fingerprint"))
    if not material_state or len(fingerprint) != 64:
        raise ValueError("merge readiness payload lacks a material observation")
    repository = _text(readiness.get("repository"))
    exact_head = _text(readiness.get("expected_exact_head")).casefold()
    if not repository or not EXACT_HEAD_PATTERN.fullmatch(exact_head):
        raise ValueError("merge readiness observation requires repository and exact head")
    return {
        "schema_version": MERGE_READINESS_OBSERVATION_SCHEMA_VERSION,
        "goal_id": validate_goal_id_path_segment(goal_id),
        "repository": repository,
        "exact_head": exact_head,
        "material_fingerprint": fingerprint,
        "material_state": dict(material_state),
        "ready": readiness.get("ready") is True,
        "blocking_reasons": _string_list(readiness.get("blocking_reasons")),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def readiness_observation_path(runtime_root: Path, goal_id: str) -> Path:
    safe_goal = validate_goal_id_path_segment(goal_id)
    return (
        runtime_root.expanduser().resolve()
        / "goals"
        / safe_goal
        / "pr_review"
        / "merge_readiness_observations.json"
    )


def read_readiness_observations(
    *, runtime_root: Path, goal_id: str
) -> dict[str, dict[str, Any]]:
    path = readiness_observation_path(runtime_root, goal_id)
    with exclusive_file_lock(path, operation="pr_review_readiness_observation_read"):
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        != MERGE_READINESS_OBSERVATION_STORE_SCHEMA_VERSION
    ):
        raise ValueError("merge readiness observation store has an unsupported schema")
    if payload.get("goal_id") != validate_goal_id_path_segment(goal_id):
        raise ValueError("merge readiness observation store belongs to another Goal")
    rows = payload.get("observations")
    if not isinstance(rows, list):
        raise TypeError("merge readiness observation store observations must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("schema_version")
            != MERGE_READINESS_OBSERVATION_SCHEMA_VERSION
        ):
            raise ValueError("merge readiness observation store contains an invalid row")
        key = observation_key(str(row.get("repository") or ""), str(row.get("exact_head") or ""))
        result[key] = dict(row)
    return result


def record_readiness_observation(
    *, runtime_root: Path, goal_id: str, readiness: Mapping[str, Any]
) -> dict[str, Any]:
    observation = build_readiness_observation(goal_id=goal_id, readiness=readiness)
    path = readiness_observation_path(runtime_root, goal_id)
    key = observation_key(observation["repository"], observation["exact_head"])
    repository_number = key.rsplit("@", 1)[0]
    with exclusive_file_lock(path, operation="pr_review_readiness_observation_write"):
        current: dict[str, dict[str, Any]] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version")
                != MERGE_READINESS_OBSERVATION_STORE_SCHEMA_VERSION
                or payload.get("goal_id") != observation["goal_id"]
            ):
                raise ValueError("merge readiness observation store is invalid")
            for row in payload.get("observations") or []:
                if isinstance(row, dict):
                    row_key = observation_key(
                        str(row.get("repository") or ""),
                        str(row.get("exact_head") or ""),
                    )
                    if row_key.rsplit("@", 1)[0] != repository_number:
                        current[row_key] = dict(row)
        current[key] = observation
        rows = sorted(
            current.values(),
            key=lambda row: str(row.get("observed_at") or ""),
            reverse=True,
        )[:MAX_OBSERVATIONS]
        atomic_write_json(
            path,
            {
                "schema_version": MERGE_READINESS_OBSERVATION_STORE_SCHEMA_VERSION,
                "goal_id": observation["goal_id"],
                "observations": rows,
            },
            preserve_mode=True,
        )
    return observation


def observation_matches_material_state(
    observation: Mapping[str, Any], material_state: Mapping[str, Any]
) -> bool:
    return bool(
        observation.get("schema_version")
        == MERGE_READINESS_OBSERVATION_SCHEMA_VERSION
        and _text(observation.get("material_fingerprint"))
        == readiness_material_fingerprint(material_state)
    )


__all__ = [
    "MERGE_READINESS_OBSERVATION_SCHEMA_VERSION",
    "MERGE_READINESS_OBSERVATION_STORE_SCHEMA_VERSION",
    "build_readiness_observation",
    "observation_key",
    "observation_matches_material_state",
    "read_readiness_observations",
    "readiness_material_fingerprint",
    "readiness_material_state",
    "record_readiness_observation",
]
