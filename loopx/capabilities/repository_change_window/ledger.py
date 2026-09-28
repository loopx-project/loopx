from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from ...file_lock import exclusive_file_lock
from ...feedback import validate_local_control_text
from ...registry import atomic_write_json
from .repository import (
    RepositoryChangeWindowError,
    git_text,
    repository_changed_paths,
    repository_change_fingerprint,
    repository_has_changes,
    repository_worktree_roots,
    resolve_repository_context,
)


EVENT_SCHEMA_VERSION = "repository_pending_change_event_v0"
LIST_SCHEMA_VERSION = "repository_pending_change_list_v0"
VERIFY_SCHEMA_VERSION = "repository_pending_change_verify_v0"
RECONCILE_SCHEMA_VERSION = "repository_pending_change_reconcile_v0"
RECONCILE_DETAIL_LIMIT = 20
LEDGER_RELATIVE_PATH = Path("repository-change-window") / "pending-change-events.jsonl"
LOCATOR_RELATIVE_PATH = (
    Path("repository-change-window") / "pending-change-locators.json"
)
_CHANGE_ID_RE = re.compile(r"^change_[a-z0-9][a-z0-9_-]{5,63}$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/#-]{0,191}$")
_ABSOLUTE_PATH_TOKEN_RE = re.compile(
    r"(?:^|[\s=:('])(?:/(?!/)|[A-Za-z]:[\\/]|\\\\)",
)
_RESOLUTIONS = {"merged", "superseded", "abandoned"}


def _now_iso(now: datetime | None = None) -> str:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise RepositoryChangeWindowError("ledger clock must be timezone-aware")
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_path(runtime_root: Path) -> Path:
    return runtime_root.expanduser() / LEDGER_RELATIVE_PATH


def _locator_path(runtime_root: Path) -> Path:
    return runtime_root.expanduser() / LOCATOR_RELATIVE_PATH


def _public_value(
    value: str | None,
    *,
    field: str,
    required: bool = False,
    max_length: int = 240,
) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            raise RepositoryChangeWindowError(f"{field} is required")
        return None
    if len(normalized) > max_length:
        raise RepositoryChangeWindowError(f"{field} exceeds {max_length} characters")
    lowered = normalized.lower()
    contains_absolute_path = (
        bool(_ABSOLUTE_PATH_TOKEN_RE.search(normalized))
        or normalized.startswith("//")
        or "file://" in lowered
    )
    if contains_absolute_path:
        raise RepositoryChangeWindowError(
            f"{field} must not contain local absolute paths"
        )
    try:
        validate_local_control_text(field, normalized)
    except ValueError as exc:
        raise RepositoryChangeWindowError(
            f"{field} must not contain credentials"
        ) from exc
    if "\n" in normalized or "\r" in normalized:
        raise RepositoryChangeWindowError(f"{field} must be one line")
    return normalized


def _optional_id(value: str | None, *, field: str) -> str | None:
    normalized = _public_value(value, field=field)
    if normalized and not _PUBLIC_ID_RE.fullmatch(normalized):
        raise RepositoryChangeWindowError(
            f"{field} must use a compact public-safe identifier"
        )
    return normalized


def _write_scopes(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _public_value(value, field="write_scope", required=True)
        assert normalized is not None
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("~"):
            raise RepositoryChangeWindowError(
                "write_scope must be a repository-relative path or glob"
            )
        if normalized not in result:
            result.append(normalized)
    return result


def _validation_refs(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _public_value(
            value,
            field="validation_ref",
            required=True,
            max_length=240,
        )
        assert normalized is not None
        if normalized not in result:
            result.append(normalized)
    return result


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), 1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RepositoryChangeWindowError(
                f"pending-change ledger line {line_number} is invalid JSON"
            ) from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != EVENT_SCHEMA_VERSION
        ):
            raise RepositoryChangeWindowError(
                f"pending-change ledger line {line_number} has an unsupported schema"
            )
        result.append(raw)
    return result


def _project(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    projection: dict[str, dict[str, Any]] = {}
    for event in events:
        change_id = str(event.get("change_id") or "")
        transition = str(event.get("transition") or "")
        if not _CHANGE_ID_RE.fullmatch(change_id):
            raise RepositoryChangeWindowError(
                "pending-change ledger contains invalid change_id"
            )
        if transition in {"recorded", "refreshed"}:
            record = event.get("record")
            if not isinstance(record, Mapping):
                raise RepositoryChangeWindowError(
                    "pending-change record event requires an object record"
                )
            projection[change_id] = dict(record)
        elif transition == "resolved":
            current = projection.get(change_id)
            if current is None:
                raise RepositoryChangeWindowError(
                    "pending-change resolution has no preceding record"
                )
            projection[change_id] = {
                **current,
                "state": "resolved",
                "resolution": event.get("resolution"),
                "resolution_evidence": event.get("evidence"),
                "superseded_by": event.get("superseded_by"),
                "resolved_at": event.get("recorded_at"),
            }
        else:
            raise RepositoryChangeWindowError(
                f"unsupported pending-change transition `{transition}`"
            )
    return projection


def _append_event(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    encoded = (
        json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _read_locators(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": "repository_pending_change_locators_v0",
            "changes": {},
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepositoryChangeWindowError(
            "pending-change locator state is invalid JSON"
        ) from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != "repository_pending_change_locators_v0"
        or not isinstance(raw.get("changes"), dict)
    ):
        raise RepositoryChangeWindowError(
            "pending-change locator state has invalid schema"
        )
    return raw


def _write_locator(
    runtime_root: Path,
    *,
    change_id: str,
    repository_id: str,
    repo_path: Path,
    checkout_kind: str,
    checkout_id: str,
    head_oid: str,
    changed_paths: Sequence[str],
    recorded_at: str,
) -> None:
    path = _locator_path(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = _read_locators(path)
    changes = dict(payload["changes"])
    changes[change_id] = {
        "repository_id": repository_id,
        "worktree_path": str(repo_path),
        "checkout_kind": checkout_kind,
        "checkout_id": checkout_id,
        "head_oid": head_oid,
        "changed_paths": list(changed_paths),
        "updated_at": recorded_at,
    }
    atomic_write_json(path, {**payload, "changes": changes})
    os.chmod(path, 0o600)


def _remove_locator(runtime_root: Path, *, change_id: str) -> None:
    path = _locator_path(runtime_root)
    if not path.exists():
        return
    payload = _read_locators(path)
    changes = dict(payload["changes"])
    changes.pop(change_id, None)
    atomic_write_json(path, {**payload, "changes": changes})
    os.chmod(path, 0o600)


def _derived_change_id(
    *,
    repository_id: str,
    checkout_kind: str,
    checkout_id: str,
) -> str:
    identity_parts = (
        (repository_id, checkout_id)
        if checkout_kind == "branch"
        else (repository_id, checkout_kind, checkout_id)
    )
    identity = "\0".join(identity_parts).encode("utf-8")
    return f"change_{hashlib.sha256(identity).hexdigest()[:20]}"


def _merged_string_list(current: object, additions: Sequence[str]) -> list[str]:
    existing = (
        [str(item) for item in current if isinstance(item, str)]
        if isinstance(current, list)
        else []
    )
    return list(dict.fromkeys([*existing, *additions]))


def _comparable_record(record: Mapping[str, Any]) -> dict[str, Any]:
    comparable = dict(record)
    comparable.pop("updated_at", None)
    comparable.pop("source", None)
    decision = comparable.get("gate_decision")
    if isinstance(decision, Mapping):
        comparable["gate_decision"] = {
            key: value for key, value in decision.items() if key != "observed_at"
        }
    return comparable


def _event_id(event: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        event,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def record_pending_change(
    *,
    runtime_root: Path,
    repo_path: str | Path,
    decision: Mapping[str, Any],
    source: str,
    goal_id: str | None = None,
    todo_id: str | None = None,
    change_id: str | None = None,
    pr_ref: str | None = None,
    write_scopes: Sequence[str] = (),
    validation_refs: Sequence[str] = (),
    supersedes: str | None = None,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    context = resolve_repository_context(repo_path)
    normalized_goal = _optional_id(goal_id, field="goal_id")
    normalized_todo = _optional_id(todo_id, field="todo_id")
    normalized_pr = _optional_id(pr_ref, field="pr_ref")
    normalized_supersedes = _optional_id(supersedes, field="supersedes")
    normalized_source = _public_value(source, field="source", required=True)
    assert normalized_source is not None
    stable_id = str(change_id or "").strip() or _derived_change_id(
        repository_id=context.repository_id,
        checkout_kind=context.checkout_kind,
        checkout_id=context.checkout_id,
    )
    if not _CHANGE_ID_RE.fullmatch(stable_id):
        raise RepositoryChangeWindowError(
            "change_id must match change_<lowercase-letters-digits-underscore-hyphen>"
        )
    recorded_at = _now_iso(now)
    changed_paths = repository_changed_paths(context)
    fingerprint = repository_change_fingerprint(context)
    bounded_decision = {
        key: decision.get(key)
        for key in (
            "schema_version",
            "allowed",
            "reason",
            "timezone",
            "observed_at",
            "next_eligible_at",
        )
        if key in decision
    }
    normalized_scopes = _write_scopes(write_scopes)
    normalized_validations = _validation_refs(validation_refs)
    path = _event_path(runtime_root)
    with exclusive_file_lock(path, operation="record_pending_repository_change"):
        events = _read_events(path)
        current = _project(events).get(stable_id)
        if current and current.get("state") == "resolved":
            raise RepositoryChangeWindowError(
                "resolved pending change cannot be reopened; use a new change_id"
            )
        current = current or {}
        record = {
            "schema_version": "repository_pending_change_v1",
            "change_id": stable_id,
            "state": "open",
            "repository_id": context.repository_id,
            "checkout_kind": context.checkout_kind,
            "checkout_id": context.checkout_id,
            "branch": context.branch,
            "head_oid": context.head_oid,
            "goal_id": normalized_goal or current.get("goal_id"),
            "todo_id": normalized_todo or current.get("todo_id"),
            "pr_ref": normalized_pr or current.get("pr_ref"),
            "write_scopes": _merged_string_list(
                current.get("write_scopes"), normalized_scopes
            ),
            "validation_refs": _merged_string_list(
                current.get("validation_refs"), normalized_validations
            ),
            "supersedes": normalized_supersedes or current.get("supersedes"),
            "source": normalized_source,
            "gate_decision": bounded_decision,
            "fingerprint": fingerprint,
            "changed_path_count": len(changed_paths),
            "private_path_inventory": "machine_private_locator",
            "updated_at": recorded_at,
            "contains_code": False,
            "contains_diff_body": False,
            "contains_local_path": False,
        }
        duplicate = bool(current) and _comparable_record(current) == _comparable_record(
            record
        )
        transition = "refreshed" if current else "recorded"
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "transition": transition,
            "change_id": stable_id,
            "recorded_at": recorded_at,
            "record": record,
        }
        event["event_id"] = _event_id(event)
        if execute and not duplicate:
            _append_event(path, event)
        if execute:
            _write_locator(
                runtime_root,
                change_id=stable_id,
                repository_id=context.repository_id,
                repo_path=context.root,
                checkout_kind=context.checkout_kind,
                checkout_id=context.checkout_id,
                head_oid=context.head_oid,
                changed_paths=changed_paths,
                recorded_at=recorded_at,
            )
    return {
        "ok": True,
        "schema_version": "repository_pending_change_record_v0",
        "dry_run": not execute,
        "changed": bool(execute and not duplicate),
        "duplicate": duplicate,
        "transition": transition,
        "storage_ref": LEDGER_RELATIVE_PATH.as_posix(),
        "locator_status": "registered" if execute else "planned",
        "record": record,
    }


def reconcile_pending_changes(
    *,
    runtime_root: Path,
    repo_path: str | Path,
    decision: Mapping[str, Any],
    include_linked_worktrees: bool = False,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    seed_context = resolve_repository_context(repo_path)
    if decision.get("allowed") is not False:
        return {
            "ok": True,
            "schema_version": RECONCILE_SCHEMA_VERSION,
            "status": "window_open_noop",
            "dry_run": not execute,
            "repository_id": seed_context.repository_id,
            "scope": "linked_worktrees" if include_linked_worktrees else "current",
            "scanned_worktree_count": 0,
            "dirty_worktree_count": 0,
            "changed_count": 0,
            "duplicate_count": 0,
            "error_count": 0,
            "changes": [],
            "contains_local_path": False,
        }

    rows: list[dict[str, Any]] = []
    scanned_count = 0
    clean_count = 0
    error_count = 0
    changed_count = 0
    duplicate_count = 0
    worktree_roots = (
        repository_worktree_roots(seed_context)
        if include_linked_worktrees
        else (seed_context.root,)
    )

    def probe(worktree_root: Path) -> tuple[Path, str]:
        if not worktree_root.is_dir():
            return worktree_root, "missing"
        try:
            return (
                worktree_root,
                "dirty" if repository_has_changes(worktree_root) else "clean",
            )
        except (OSError, TypeError, ValueError):
            return worktree_root, "unreadable"

    worker_count = min(8, max(1, len(worktree_roots)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        probe_results = list(executor.map(probe, worktree_roots))

    for worktree_root, probe_status in probe_results:
        if probe_status in {"missing", "unreadable"}:
            error_count += 1
            continue
        scanned_count += 1
        if probe_status == "clean":
            clean_count += 1
            continue
        try:
            context = resolve_repository_context(worktree_root)
            if (
                context.common_dir != seed_context.common_dir
                or context.repository_id != seed_context.repository_id
            ):
                error_count += 1
                continue
            changed_paths = repository_changed_paths(context)
            if not changed_paths:
                clean_count += 1
                continue
            result = record_pending_change(
                runtime_root=runtime_root,
                repo_path=context.root,
                decision=decision,
                source="reconcile_cli",
                execute=execute,
                now=now,
            )
        except (OSError, TypeError, ValueError):
            error_count += 1
            continue
        record = result["record"]
        if result["changed"]:
            changed_count += 1
        if result["duplicate"]:
            duplicate_count += 1
        rows.append(
            {
                "change_id": record["change_id"],
                "repository_id": record["repository_id"],
                "checkout_kind": record["checkout_kind"],
                "checkout_id": record["checkout_id"],
                "head_oid": record["head_oid"],
                "changed_path_count": record["changed_path_count"],
                "transition": result["transition"],
                "changed": result["changed"],
                "duplicate": result["duplicate"],
            }
        )
    rows.sort(key=lambda item: (str(item["checkout_kind"]), str(item["checkout_id"])))
    visible_rows = rows[:RECONCILE_DETAIL_LIMIT]
    return {
        "ok": error_count == 0,
        "schema_version": RECONCILE_SCHEMA_VERSION,
        "status": "reconciled" if execute else "reconcile_preview",
        "dry_run": not execute,
        "repository_id": seed_context.repository_id,
        "scope": "linked_worktrees" if include_linked_worktrees else "current",
        "probe_worker_count": worker_count,
        "scanned_worktree_count": scanned_count,
        "clean_worktree_count": clean_count,
        "dirty_worktree_count": len(rows),
        "changed_count": changed_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "changes": visible_rows,
        "change_detail_limit": RECONCILE_DETAIL_LIMIT,
        "omitted_change_count": len(rows) - len(visible_rows),
        "storage_ref": LEDGER_RELATIVE_PATH.as_posix(),
        "private_locator_inventory": LOCATOR_RELATIVE_PATH.as_posix(),
        "contains_local_path": False,
    }


def list_pending_changes(
    *,
    runtime_root: Path,
    state: str = "open",
    repository_id: str | None = None,
) -> dict[str, Any]:
    if state not in {"open", "resolved", "all"}:
        raise RepositoryChangeWindowError("state must be open, resolved, or all")
    normalized_repository = _public_value(repository_id, field="repository_id")
    records = list(_project(_read_events(_event_path(runtime_root))).values())
    records = [
        record
        for record in records
        if (state == "all" or record.get("state") == state)
        and (
            normalized_repository is None
            or record.get("repository_id") == normalized_repository
        )
    ]
    records.sort(
        key=lambda item: (str(item.get("updated_at") or ""), str(item["change_id"]))
    )
    return {
        "ok": True,
        "schema_version": LIST_SCHEMA_VERSION,
        "state": state,
        "storage_ref": LEDGER_RELATIVE_PATH.as_posix(),
        "count": len(records),
        "changes": records,
    }


def resolve_pending_change(
    *,
    runtime_root: Path,
    change_id: str,
    resolution: str,
    evidence: str,
    superseded_by: str | None = None,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _CHANGE_ID_RE.fullmatch(change_id):
        raise RepositoryChangeWindowError("invalid change_id")
    if resolution not in _RESOLUTIONS:
        raise RepositoryChangeWindowError(
            "resolution must be merged, superseded, or abandoned"
        )
    normalized_evidence = _public_value(
        evidence,
        field="evidence",
        required=True,
        max_length=320,
    )
    assert normalized_evidence is not None
    normalized_successor = _optional_id(superseded_by, field="superseded_by")
    if resolution == "superseded" and not normalized_successor:
        raise RepositoryChangeWindowError(
            "superseded resolution requires superseded_by"
        )
    recorded_at = _now_iso(now)
    path = _event_path(runtime_root)
    with exclusive_file_lock(path, operation="resolve_pending_repository_change"):
        events = _read_events(path)
        current = _project(events).get(change_id)
        if current is None:
            raise RepositoryChangeWindowError(f"unknown pending change `{change_id}`")
        already_resolved = current.get("state") == "resolved"
        duplicate = bool(
            already_resolved
            and current.get("resolution") == resolution
            and current.get("resolution_evidence") == normalized_evidence
            and current.get("superseded_by") == normalized_successor
        )
        if already_resolved and not duplicate:
            raise RepositoryChangeWindowError(
                "pending change already has a different terminal resolution"
            )
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "transition": "resolved",
            "change_id": change_id,
            "recorded_at": recorded_at,
            "resolution": resolution,
            "evidence": normalized_evidence,
            "superseded_by": normalized_successor,
        }
        event["event_id"] = _event_id(event)
        if execute and not duplicate:
            _append_event(path, event)
        if execute:
            _remove_locator(runtime_root, change_id=change_id)
    return {
        "ok": True,
        "schema_version": "repository_pending_change_resolution_v0",
        "dry_run": not execute,
        "changed": bool(execute and not duplicate),
        "duplicate": duplicate,
        "change_id": change_id,
        "resolution": resolution,
        "evidence": normalized_evidence,
        "superseded_by": normalized_successor,
        "storage_ref": LEDGER_RELATIVE_PATH.as_posix(),
    }


def verify_pending_change(
    *,
    runtime_root: Path,
    change_id: str,
) -> dict[str, Any]:
    if not _CHANGE_ID_RE.fullmatch(change_id):
        raise RepositoryChangeWindowError("invalid change_id")
    current = _project(_read_events(_event_path(runtime_root))).get(change_id)
    if current is None:
        raise RepositoryChangeWindowError(f"unknown pending change `{change_id}`")
    locators = _read_locators(_locator_path(runtime_root))["changes"]
    locator = locators.get(change_id)
    checks: list[dict[str, object]] = []
    if not isinstance(locator, Mapping):
        checks.append({"check": "worktree_locator", "ok": False, "status": "missing"})
        return {
            "ok": False,
            "schema_version": VERIFY_SCHEMA_VERSION,
            "change_id": change_id,
            "status": "unlocatable",
            "checks": checks,
        }
    repo_path = Path(str(locator.get("worktree_path") or ""))
    if not repo_path.is_dir():
        checks.append({"check": "worktree", "ok": False, "status": "missing"})
        return {
            "ok": False,
            "schema_version": VERIFY_SCHEMA_VERSION,
            "change_id": change_id,
            "status": "missing_worktree",
            "checks": checks,
        }
    context = resolve_repository_context(repo_path)
    repository_matches = context.repository_id == current.get("repository_id")
    checks.append(
        {
            "check": "repository_identity",
            "ok": repository_matches,
            "status": "match" if repository_matches else "mismatch",
        }
    )
    branch = str(current.get("branch") or "")
    checkout_kind = str(current.get("checkout_kind") or "branch")
    checkout_id = str(current.get("checkout_id") or branch)
    checkout_matches = (
        context.checkout_kind == checkout_kind and context.checkout_id == checkout_id
    )
    checks.append(
        {
            "check": "checkout_identity",
            "ok": checkout_matches,
            "status": "match" if checkout_matches else "mismatch",
        }
    )
    recorded_head = str(current.get("head_oid") or "")
    head_exists = bool(
        git_text(
            context.root,
            "rev-parse",
            "--verify",
            f"{recorded_head}^{{commit}}",
            check=False,
        )
    )
    checks.append(
        {
            "check": "recorded_head",
            "ok": head_exists,
            "status": "present" if head_exists else "missing",
        }
    )
    checkout_head_ok = False
    if checkout_kind == "branch":
        branch_head = git_text(
            context.root,
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}^{{commit}}",
            check=False,
        )
        branch_exists = bool(branch_head)
        checks.append(
            {
                "check": "branch",
                "ok": branch_exists,
                "status": "present" if branch_exists else "missing",
            }
        )
        head_status = "missing"
        if branch_exists and head_exists:
            head_status = (
                "unchanged" if branch_head == recorded_head else "advanced_or_rewritten"
            )
        checkout_head_ok = head_status == "unchanged"
        checks.append(
            {
                "check": "branch_head",
                "ok": checkout_head_ok,
                "status": head_status,
            }
        )
    else:
        head_status = (
            "unchanged"
            if checkout_matches and context.head_oid == recorded_head
            else "advanced_or_rewritten"
        )
        checkout_head_ok = head_exists and head_status == "unchanged"
        checks.append(
            {
                "check": "detached_head",
                "ok": checkout_head_ok,
                "status": head_status,
            }
        )
    fingerprint_status = "not_current_checkout"
    if checkout_matches:
        current_fingerprint = repository_change_fingerprint(context)
        recorded_fingerprint = current.get("fingerprint")
        fingerprint_status = (
            "unchanged"
            if isinstance(recorded_fingerprint, Mapping)
            and recorded_fingerprint.get("digest") == current_fingerprint.get("digest")
            else "changed"
        )
    checks.append(
        {
            "check": "worktree_fingerprint",
            "ok": fingerprint_status == "unchanged",
            "status": fingerprint_status,
        }
    )
    recorded_paths = locator.get("changed_paths")
    path_inventory_ok = True
    if isinstance(recorded_paths, list) and all(
        isinstance(item, str) for item in recorded_paths
    ):
        current_paths = repository_changed_paths(context)
        path_inventory_ok = tuple(recorded_paths) == current_paths
        checks.append(
            {
                "check": "private_changed_path_inventory",
                "ok": path_inventory_ok,
                "status": "unchanged" if path_inventory_ok else "changed",
                "recorded_count": len(recorded_paths),
                "current_count": len(current_paths),
            }
        )
    else:
        checks.append(
            {
                "check": "private_changed_path_inventory",
                "ok": True,
                "status": "legacy_absent",
            }
        )
    ok = (
        repository_matches
        and checkout_matches
        and head_exists
        and checkout_head_ok
        and fingerprint_status == "unchanged"
        and path_inventory_ok
    )
    return {
        "ok": ok,
        "schema_version": VERIFY_SCHEMA_VERSION,
        "change_id": change_id,
        "status": "verified" if ok else "drifted",
        "state": current.get("state"),
        "checks": checks,
        "contains_local_path": False,
    }
