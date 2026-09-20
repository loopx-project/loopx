from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ...registry import atomic_write_json, read_json


PUBLICATION_CANDIDATE_SCHEMA = "periodic_report_publication_candidate_v0"
PUBLICATION_CURSOR_SCHEMA = "periodic_report_publication_cursor_v0"
INCREMENTAL_BASELINE_SCHEMA = "periodic_report_incremental_baseline_v0"

_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _identity(value: object, *, prefix: str) -> str:
    return f"{prefix}_{_canonical_digest(value).split(':', 1)[1][:24]}"


def _required_text(value: object, label: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _identity_text(value: object, label: str) -> str:
    text = _required_text(value, label, maximum=160)
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{label} must be a stable public identity")
    return text


def _timestamp(value: object, label: str) -> str:
    text = _required_text(value, label, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: object, label: str) -> str:
    digest = _required_text(value, label, maximum=80)
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} must use sha256")
    return digest


def _fact_state(raw: Mapping[str, Any], *, label: str) -> dict[str, str]:
    source_ref = _required_text(raw.get("source_ref"), f"{label}.source_ref")
    status = _required_text(
        raw.get("status") or _effective_status(raw),
        f"{label}.status",
        maximum=80,
    )
    fingerprint = _digest(raw.get("fact_fingerprint"), f"{label}.fact_fingerprint")
    content_kind = _required_text(
        raw.get("content_kind") or _effective_content_kind(raw),
        f"{label}.content_kind",
        maximum=80,
    )
    return {
        "source_ref": source_ref,
        "status": status,
        "content_kind": content_kind,
        "fact_fingerprint": fingerprint,
    }


def _effective_status(item: Mapping[str, Any]) -> str:
    supplied = str(item.get("status") or "").strip()
    if supplied:
        return supplied
    return "open" if item.get("content_kind") == "next_action" else "done"


def _effective_content_kind(item: Mapping[str, Any]) -> str:
    supplied = str(item.get("content_kind") or "").strip()
    if supplied:
        return supplied
    return "next_action" if _effective_status(item) == "open" else "outcome"


def periodic_report_fact_fingerprint(item: Mapping[str, Any]) -> str:
    """Return the stable semantic identity of one reportable project fact."""

    identity = {
        key: " ".join(str(item.get(key) or "").split())
        for key in ("source_ref", "title", "summary", "content_kind", "status")
    }
    identity["status"] = _effective_status(item)
    identity["content_kind"] = _effective_content_kind(item)
    if not identity["source_ref"]:
        raise ValueError("periodic-report fact source_ref is required")
    return _canonical_digest(identity)


def _cursor_directory(runtime_root: Path, goal_id: str) -> Path:
    safe_goal = _identity_text(goal_id, "goal_id")
    return (
        runtime_root.expanduser().resolve()
        / "goals"
        / safe_goal
        / "periodic_reports"
        / "publication-cursors"
    )


def _cursor_path(runtime_root: Path, goal_id: str, agent_id: str) -> Path:
    safe_agent = _identity_text(agent_id, "agent_id")
    return _cursor_directory(runtime_root, goal_id) / f"{safe_agent}.json"


def normalize_periodic_report_publication_cursor(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    if raw.get("schema_version") != PUBLICATION_CURSOR_SCHEMA:
        raise ValueError(f"publication cursor must use {PUBLICATION_CURSOR_SCHEMA}")
    goal_id = _identity_text(raw.get("goal_id"), "publication_cursor.goal_id")
    agent_id = _identity_text(raw.get("agent_id"), "publication_cursor.agent_id")
    generation_id = _identity_text(
        raw.get("generation_id"), "publication_cursor.generation_id"
    )
    publication_id = _identity_text(
        raw.get("publication_id"), "publication_cursor.publication_id"
    )
    delivered_at = _timestamp(
        raw.get("delivered_at"), "publication_cursor.delivered_at"
    )
    covered_until = _timestamp(
        raw.get("covered_until"), "publication_cursor.covered_until"
    )
    covered = sorted(
        {
            _identity_text(value, "publication_cursor.covered_trigger_ids[]")
            for value in raw.get("covered_trigger_ids") or []
        }
    )
    facts: dict[str, dict[str, str]] = {}
    raw_facts = raw.get("fact_states")
    if not isinstance(raw_facts, Sequence) or isinstance(raw_facts, (str, bytes)):
        raise ValueError("publication_cursor.fact_states must be a list")
    for index, value in enumerate(raw_facts):
        if not isinstance(value, Mapping):
            raise ValueError("publication_cursor.fact_states must contain objects")
        fact = _fact_state(value, label=f"publication_cursor.fact_states[{index}]")
        if fact["source_ref"] in facts:
            raise ValueError("publication_cursor.fact_states contains duplicates")
        facts[fact["source_ref"]] = fact
    normalized: dict[str, Any] = {
        "schema_version": PUBLICATION_CURSOR_SCHEMA,
        "cursor_id": "",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "generation_id": generation_id,
        "publication_id": publication_id,
        "delivered_at": delivered_at,
        "covered_until": covered_until,
        "covered_trigger_ids": covered,
        "fact_states": [facts[key] for key in sorted(facts)],
    }
    predecessor = str(raw.get("predecessor_publication_id") or "").strip()
    if predecessor:
        normalized["predecessor_publication_id"] = _identity_text(
            predecessor, "publication_cursor.predecessor_publication_id"
        )
    workspace_digest = str(raw.get("workspace_projection_sha256") or "").strip()
    if workspace_digest:
        normalized["workspace_projection_sha256"] = _digest(
            workspace_digest, "publication_cursor.workspace_projection_sha256"
        )
    normalized["cursor_id"] = _identity(
        {key: value for key, value in normalized.items() if key != "cursor_id"},
        prefix="report_cursor",
    )
    if raw.get("cursor_id") != normalized["cursor_id"]:
        raise ValueError("publication_cursor.cursor_id does not match contents")
    return normalized


def read_periodic_report_publication_cursor(
    *, runtime_root: Path, goal_id: str, agent_id: str
) -> dict[str, Any] | None:
    path = _cursor_path(runtime_root, goal_id, agent_id)
    return _read_periodic_report_publication_cursor_path(
        path=path,
        goal_id=goal_id,
        agent_id=agent_id,
    )


def read_periodic_report_goal_publication_cursors(
    *, runtime_root: Path, goal_id: str
) -> list[dict[str, Any]]:
    """Read every lane's published baseline that one Goal has recorded.

    A report covers the Goal, so what the Goal already announced is a Goal-level
    fact even though each lane keeps its own cursor file. A cursor that cannot
    be normalized raises rather than being skipped: narrowing the published set
    silently would re-announce the facts that file was suppressing.
    """
    directory = _cursor_directory(runtime_root, goal_id)
    if not directory.is_dir():
        return []
    cursors: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        cursor = _read_periodic_report_publication_cursor_path(
            path=path,
            goal_id=goal_id,
            agent_id=path.stem,
        )
        if cursor is not None:
            cursors.append(cursor)
    return cursors


def _read_periodic_report_publication_cursor_path(
    *, path: Path, goal_id: str, agent_id: str
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ValueError("periodic-report publication cursor must be an object")
    cursor = normalize_periodic_report_publication_cursor(value)
    if cursor["goal_id"] != goal_id or cursor["agent_id"] != agent_id:
        raise ValueError("periodic-report publication cursor identity changed")
    return cursor


def periodic_report_incremental_baseline(
    cursor: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if cursor is None:
        return None
    normalized = normalize_periodic_report_publication_cursor(cursor)
    return _normalize_incremental_baseline(
        {
            "schema_version": INCREMENTAL_BASELINE_SCHEMA,
            "cursor_id": normalized["cursor_id"],
            "predecessor_generation_id": normalized["generation_id"],
            "predecessor_publication_id": normalized["publication_id"],
            "delivered_at": normalized["delivered_at"],
            "covered_until": normalized["covered_until"],
        }
    )


def _normalize_incremental_baseline(raw: Mapping[str, Any]) -> dict[str, str]:
    if raw.get("schema_version") != INCREMENTAL_BASELINE_SCHEMA:
        raise ValueError(f"incremental baseline must use {INCREMENTAL_BASELINE_SCHEMA}")
    return {
        "schema_version": INCREMENTAL_BASELINE_SCHEMA,
        "cursor_id": _identity_text(raw.get("cursor_id"), "baseline.cursor_id"),
        "predecessor_generation_id": _identity_text(
            raw.get("predecessor_generation_id"),
            "baseline.predecessor_generation_id",
        ),
        "predecessor_publication_id": _identity_text(
            raw.get("predecessor_publication_id"),
            "baseline.predecessor_publication_id",
        ),
        "delivered_at": _timestamp(raw.get("delivered_at"), "baseline.delivered_at"),
        "covered_until": _timestamp(raw.get("covered_until"), "baseline.covered_until"),
    }


def select_incremental_project_progress(
    snapshot: Mapping[str, Any],
    *,
    cursor: Mapping[str, Any] | None,
    goal_cursors: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Select only facts the Goal has not already published in this exact form.

    ``cursor`` is the calling lane's own baseline and decides ``changed``.
    ``goal_cursors`` carries every lane's baseline for the same Goal, so a fact
    another lane already announced does not become new once more.
    """

    items = snapshot.get("items")
    if not isinstance(items, list):
        raise ValueError("periodic-report progress snapshot items are invalid")
    previous = {}
    baseline = periodic_report_incremental_baseline(cursor)
    if cursor is not None:
        normalized = normalize_periodic_report_publication_cursor(cursor)
        previous = {item["source_ref"]: item for item in normalized["fact_states"]}
    published: set[tuple[str, str]] = set()
    # Passing this lane's own cursor in is harmless: every fact it holds is
    # already in ``previous``, so it cannot hide anything the checks below miss.
    for sibling in goal_cursors or []:
        sibling_cursor = normalize_periodic_report_publication_cursor(sibling)
        published.update(
            (item["source_ref"], item["fact_fingerprint"])
            for item in sibling_cursor["fact_states"]
        )
    selected: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ValueError("periodic-report progress snapshot item is invalid")
        item = dict(raw)
        fingerprint = periodic_report_fact_fingerprint(item)
        source_ref = str(item.get("source_ref") or "").strip()
        prior = previous.get(source_ref)
        if prior and prior["fact_fingerprint"] == fingerprint:
            continue
        # Another lane already announced this exact fact to the same Goal.
        if prior is None and (source_ref, fingerprint) in published:
            continue
        item["fact_fingerprint"] = fingerprint
        item["change_kind"] = "changed" if prior else "added"
        if prior:
            item["previous_status"] = prior["status"]
            item["previous_content_kind"] = prior["content_kind"]
            item["previous_fact_fingerprint"] = prior["fact_fingerprint"]
        selected.append(item)
    if not selected:
        return None
    result = dict(snapshot)
    result["items"] = selected
    if baseline is not None:
        result["incremental_baseline"] = baseline
    return result


def build_periodic_report_publication_candidate(
    *,
    goal_id: str,
    agent_id: str,
    generation_id: str,
    trigger_receipt: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any] | None,
    workspace_projection_sha256: str | None = None,
) -> dict[str, Any]:
    normalized_facts: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(facts):
        item = dict(raw)
        item.setdefault("fact_fingerprint", periodic_report_fact_fingerprint(item))
        state = _fact_state(item, label=f"facts[{index}]")
        normalized_facts[state["source_ref"]] = state
    covered = sorted(
        {
            _identity_text(value, "trigger_receipt.coalesced_trigger_ids[]")
            for value in trigger_receipt.get("coalesced_trigger_ids") or []
        }
    )
    candidate: dict[str, Any] = {
        "schema_version": PUBLICATION_CANDIDATE_SCHEMA,
        "candidate_id": "",
        "goal_id": _identity_text(goal_id, "goal_id"),
        "agent_id": _identity_text(agent_id, "agent_id"),
        "generation_id": _identity_text(generation_id, "generation_id"),
        "covered_trigger_ids": covered,
        "fact_states": [normalized_facts[key] for key in sorted(normalized_facts)],
    }
    if baseline is not None:
        candidate["incremental_baseline"] = _normalize_incremental_baseline(baseline)
    if workspace_projection_sha256:
        candidate["workspace_projection_sha256"] = _digest(
            workspace_projection_sha256, "workspace_projection_sha256"
        )
    candidate["candidate_id"] = _identity(
        {key: value for key, value in candidate.items() if key != "candidate_id"},
        prefix="report_candidate",
    )
    return candidate


def write_periodic_report_publication_candidate(
    *, path: Path, candidate: Mapping[str, Any]
) -> None:
    if candidate.get("schema_version") != PUBLICATION_CANDIDATE_SCHEMA:
        raise ValueError(
            f"publication candidate must use {PUBLICATION_CANDIDATE_SCHEMA}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, dict(candidate))


def find_periodic_report_publication_candidate(
    *, runtime_root: Path, goal_id: str, generation_id: str
) -> dict[str, Any] | None:
    safe_goal = _identity_text(goal_id, "goal_id")
    safe_generation = _identity_text(generation_id, "generation_id")
    root = (
        runtime_root.expanduser().resolve() / "goals" / safe_goal / "periodic_reports"
    )
    matches: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return None
    for path in root.glob("**/publication-candidate.json"):
        value = read_json(path)
        if not isinstance(value, Mapping):
            raise ValueError("periodic-report publication candidate must be an object")
        if (
            value.get("goal_id") != safe_goal
            or value.get("generation_id") != safe_generation
        ):
            continue
        candidate_id = _identity_text(value.get("candidate_id"), "candidate_id")
        matches[candidate_id] = dict(value)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("generation resolves to conflicting publication candidates")
    return next(iter(matches.values()))


def commit_periodic_report_publication_cursor(
    *,
    runtime_root: Path,
    candidate: Mapping[str, Any],
    publication_id: str,
    delivered_at: str,
    covered_until: str,
) -> dict[str, Any]:
    if candidate.get("schema_version") != PUBLICATION_CANDIDATE_SCHEMA:
        raise ValueError(
            f"publication candidate must use {PUBLICATION_CANDIDATE_SCHEMA}"
        )
    supplied = dict(candidate)
    candidate_id = _identity_text(
        supplied.pop("candidate_id", None), "candidate.candidate_id"
    )
    allowed_fields = {
        "schema_version",
        "goal_id",
        "agent_id",
        "generation_id",
        "covered_trigger_ids",
        "fact_states",
        "incremental_baseline",
        "workspace_projection_sha256",
    }
    unknown = sorted(set(supplied) - allowed_fields)
    if unknown:
        raise ValueError(
            "publication candidate contains unsupported fields: " + ", ".join(unknown)
        )
    goal_id = _identity_text(candidate.get("goal_id"), "candidate.goal_id")
    agent_id = _identity_text(candidate.get("agent_id"), "candidate.agent_id")
    generation_id = _identity_text(
        candidate.get("generation_id"), "candidate.generation_id"
    )
    expected_candidate_id = _identity(supplied, prefix="report_candidate")
    if candidate_id != expected_candidate_id:
        raise ValueError("publication candidate identity does not match contents")
    normalized_publication_id = _identity_text(publication_id, "publication_id")
    path = _cursor_path(runtime_root, goal_id, agent_id)
    with exclusive_file_lock(
        path,
        policy=LockAcquisitionPolicy.MUTATION,
        agent_id=agent_id,
        operation="periodic_report_publication_cursor_commit",
    ):
        previous = _read_periodic_report_publication_cursor_path(
            path=path,
            goal_id=goal_id,
            agent_id=agent_id,
        )
        if (
            previous is not None
            and previous["generation_id"] == generation_id
            and previous["publication_id"] == normalized_publication_id
        ):
            return previous
        raw_baseline = candidate.get("incremental_baseline")
        baseline = (
            _normalize_incremental_baseline(raw_baseline)
            if isinstance(raw_baseline, Mapping)
            else None
        )
        if previous is None:
            if raw_baseline is not None:
                raise ValueError(
                    "publication candidate baseline does not match an empty cursor"
                )
        elif baseline is None or any(
            baseline.get(key) != previous[expected]
            for key, expected in (
                ("cursor_id", "cursor_id"),
                ("predecessor_generation_id", "generation_id"),
                ("predecessor_publication_id", "publication_id"),
                ("covered_until", "covered_until"),
            )
        ):
            raise ValueError(
                "publication candidate baseline does not match the current cursor"
            )
        facts = {
            item["source_ref"]: item for item in (previous or {}).get("fact_states", [])
        }
        for index, raw in enumerate(candidate.get("fact_states") or []):
            state = _fact_state(raw, label=f"candidate.fact_states[{index}]")
            facts[state["source_ref"]] = state
        covered = sorted(
            set((previous or {}).get("covered_trigger_ids", []))
            | {
                _identity_text(value, "candidate.covered_trigger_ids[]")
                for value in candidate.get("covered_trigger_ids") or []
            }
        )
        cursor: dict[str, Any] = {
            "schema_version": PUBLICATION_CURSOR_SCHEMA,
            "cursor_id": "",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "generation_id": generation_id,
            "publication_id": normalized_publication_id,
            "delivered_at": _timestamp(delivered_at, "delivered_at"),
            "covered_until": _timestamp(covered_until, "covered_until"),
            "covered_trigger_ids": covered,
            "fact_states": [facts[key] for key in sorted(facts)],
        }
        if previous is not None:
            cursor["predecessor_publication_id"] = previous["publication_id"]
        workspace_digest = str(
            candidate.get("workspace_projection_sha256") or ""
        ).strip()
        if workspace_digest:
            cursor["workspace_projection_sha256"] = _digest(
                workspace_digest, "candidate.workspace_projection_sha256"
            )
        cursor["cursor_id"] = _identity(
            {key: value for key, value in cursor.items() if key != "cursor_id"},
            prefix="report_cursor",
        )
        normalized = normalize_periodic_report_publication_cursor(cursor)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, normalized)
        return normalized


__all__ = [
    "INCREMENTAL_BASELINE_SCHEMA",
    "PUBLICATION_CANDIDATE_SCHEMA",
    "PUBLICATION_CURSOR_SCHEMA",
    "build_periodic_report_publication_candidate",
    "commit_periodic_report_publication_cursor",
    "find_periodic_report_publication_candidate",
    "normalize_periodic_report_publication_cursor",
    "periodic_report_fact_fingerprint",
    "periodic_report_incremental_baseline",
    "read_periodic_report_goal_publication_cursors",
    "read_periodic_report_publication_cursor",
    "select_incremental_project_progress",
    "write_periodic_report_publication_candidate",
]
