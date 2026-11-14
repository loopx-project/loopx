"""Read-only Steward Fast-Path portfolio triage.

Fast-Path portfolio triage answers lightweight personal/Steward inspection
intents directly from canonical Todo and run-index readback without:
- acquiring or releasing Goal/Todo task leases,
- opening or compacting upstream model session history,
- mutating Chat Session/Turn state, or
- spending Quota monitor/turn envelopes.

Authorization remains fail-closed: Steward conversation bindings are re-verified
through Core before reading portfolio state, and requested Goal IDs outside the
authorized Steward scope are rejected with zero state reads or mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...control_plane.coordination.local_authority import (
    CanonicalAuthorityRequired,
    LocalCoordinationAuthorityUnavailable,
)
from ...control_plane.projects.registry_codec import load_registry
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...control_plane.runtime.time import chronology_key
from ...control_plane.todos.contract import (
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_USER_ACTION,
    TODO_TASK_CLASS_USER_GATE,
    decision_scope_metadata_value,
)
from ...control_plane.todos.list_readback import list_goal_todos
from ...history import load_index_snapshot
from ...paths import resolve_runtime_root
from ...registry import registry_goals

PORTFOLIO_TRIAGE_SCHEMA_VERSION = "steward_portfolio_triage_v0"
FAST_PATH_RESPONSE_SCHEMA_VERSION = "steward_fast_path_response_v0"

VALID_TRIAGE_FOCUS = frozenset({"all", "gates", "todos"})
ACTIONABLE_USER_TASK_CLASSES = frozenset(
    {TODO_TASK_CLASS_USER_GATE, TODO_TASK_CLASS_USER_ACTION}
)
ACTIVE_TRIAGE_STATUSES = frozenset({TODO_STATUS_OPEN, TODO_STATUS_BLOCKED})
HIGH_PRIORITIES = frozenset({"P0", "P1"})
WITHHELD_TEXT = "(text withheld from digest; inspect the record)"

_GOAL_ID_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRIORITY_PREFIX = re.compile(r"^\s*\[(P[0-3])\]\s*", re.IGNORECASE)
_SLASH_INTENT_MAP = {
    "/triage": "all",
    "/inbox": "all",
    "/digest": "all",
    "/gates": "gates",
    "/decisions": "gates",
    "/todos": "todos",
    "/blockers": "todos",
}
_EXACT_TEXT_INTENT_MAP = {
    "triage": "all",
    "inbox": "all",
    "open gates": "gates",
    "pending gates": "gates",
    "user gates": "gates",
    "收件箱": "all",
    "快车道": "all",
    "待办速览": "todos",
    "卡点速览": "gates",
    "待确认门禁": "gates",
}


class StewardFastPathError(ValueError):
    """Typed fail-closed error for Steward Fast-Path and gate settlement."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class StewardFastPathIntent:
    """Parsed lightweight read-only Steward triage intent."""

    command: str
    focus: str
    requested_goal_ids: tuple[str, ...] = ()


def parse_steward_fast_path_intent(text: str) -> StewardFastPathIntent | None:
    """Classify explicit lightweight triage intents; return None for normal Turns."""

    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in _EXACT_TEXT_INTENT_MAP:
        return StewardFastPathIntent(
            command=lowered,
            focus=_EXACT_TEXT_INTENT_MAP[lowered],
        )

    if not normalized.startswith("/"):
        return None

    tokens = normalized.split(" ")
    head = tokens[0].lower()
    focus = _SLASH_INTENT_MAP.get(head)
    if focus is None:
        return None

    requested_goals: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lower_token = token.lower()
        if lower_token in {"all", "gates", "todos"} and head == "/triage":
            focus = lower_token
            index += 1
            continue
        if lower_token in {"--goal", "--goal-id"}:
            if index + 1 >= len(tokens):
                raise StewardFastPathError(
                    "fast-path --goal requires a Goal ID",
                    code="invalid_fast_path_arguments",
                )
            goal_token = tokens[index + 1].strip()
            if not _GOAL_ID_TOKEN.fullmatch(goal_token):
                raise StewardFastPathError(
                    "fast-path Goal ID must use public-safe identifier characters",
                    code="invalid_fast_path_arguments",
                )
            requested_goals.append(goal_token)
            index += 2
            continue
        if lower_token.startswith("goal:"):
            goal_token = token[5:].strip()
            if not _GOAL_ID_TOKEN.fullmatch(goal_token):
                raise StewardFastPathError(
                    "fast-path Goal ID must use public-safe identifier characters",
                    code="invalid_fast_path_arguments",
                )
            requested_goals.append(goal_token)
            index += 1
            continue
        # Unknown trailing prose means the user wrote a richer instruction; hand
        # off to the full Steward Turn instead of guessing or dropping context.
        return None

    deduped_goals = tuple(dict.fromkeys(requested_goals))
    return StewardFastPathIntent(
        command=head,
        focus=focus,
        requested_goal_ids=deduped_goals,
    )


def _effective_priority(todo: Mapping[str, Any]) -> str | None:
    explicit = str(todo.get("priority") or "").strip().upper()
    if explicit in {"P0", "P1", "P2", "P3"}:
        return explicit
    text = str(todo.get("text") or "")
    match = _PRIORITY_PREFIX.match(text)
    if match:
        return match.group(1).upper()
    return None


def _strip_priority_prefix(text: object) -> str:
    value = str(text or "")
    while True:
        stripped = _PRIORITY_PREFIX.sub("", value, count=1)
        if stripped == value:
            return value
        value = stripped


def _priority_rank(priority: str | None) -> int:
    if priority == "P0":
        return 0
    if priority == "P1":
        return 1
    if priority == "P2":
        return 2
    if priority == "P3":
        return 3
    return 4


def _compact_public(value: object, *, limit: int) -> str:
    compact = public_safe_compact_text(str(value or ""), limit=limit)
    return (compact or "").strip()


def _decision_scope_summary(scope: object) -> str | None:
    if not isinstance(scope, Mapping):
        return None
    kind = str(scope.get("kind") or "").strip()
    granularity = str(scope.get("granularity") or "").strip()
    key = str(scope.get("scope_key") or scope.get("key") or "").strip()
    parts = [item for item in (kind, granularity, key) if item]
    if not parts:
        return None
    return _compact_public(":".join(parts), limit=96) or None


def compute_user_gate_state_fingerprint(
    goal_id: str,
    todo: Mapping[str, Any],
) -> str:
    """Compute a deterministic canonical fingerprint for callback drift checks."""

    canonical_payload = {
        "schema_version": "steward_user_gate_fingerprint_v0",
        "goal_id": str(goal_id),
        "todo_id": str(todo.get("todo_id") or ""),
        "role": str(todo.get("role") or ""),
        "status": str(todo.get("status") or ""),
        "done": bool(todo.get("done")),
        "superseded_by": str(todo.get("superseded_by") or ""),
        "task_class": str(todo.get("task_class") or ""),
        "text": str(todo.get("text") or ""),
        "decision_scope": decision_scope_metadata_value(todo.get("decision_scope")),
        "unblocks_todo_id": str(todo.get("unblocks_todo_id") or ""),
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _select_scoped_goals(
    *,
    authorized_goal_ids: Sequence[str],
    requested_goal_ids: Sequence[str] | None,
) -> list[str]:
    authorized = list(
        dict.fromkeys(
            str(goal_id).strip()
            for goal_id in authorized_goal_ids
            if str(goal_id).strip()
        )
    )
    if not requested_goal_ids:
        return authorized
    authorized_set = set(authorized)
    selected: list[str] = []
    for raw in requested_goal_ids:
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        if candidate not in authorized_set:
            raise StewardFastPathError(
                f"goal {candidate!r} is outside the authorized triage scope",
                code="goal_outside_authorized_scope",
                details={"goal_id": candidate},
            )
        if candidate not in selected:
            selected.append(candidate)
    return selected


def resolve_all_registered_goal_ids(registry_path: Path) -> list[str]:
    """Return active/registered non-internal portfolio Goal IDs from registry."""

    registry = load_registry(registry_path)
    result: list[str] = []
    for goal in registry_goals(registry):
        goal_id = str(goal.get("id") or "").strip()
        if not goal_id or goal_id.startswith("steward-agent-"):
            continue
        if goal_id not in result:
            result.append(goal_id)
    return result


def build_portfolio_triage_projection(
    *,
    registry_path: Path,
    runtime_root_arg: str | None = None,
    authorized_goal_ids: Sequence[str],
    requested_goal_ids: Sequence[str] | None = None,
    focus: str = "all",
    max_gates: int = 8,
    max_todos: int = 8,
    max_progress: int = 5,
    max_text_chars: int = 180,
) -> dict[str, Any]:
    """Build a bounded, public-safe, read-only triage digest over scoped Goals."""

    if focus not in VALID_TRIAGE_FOCUS:
        raise StewardFastPathError(
            "triage focus must be one of: all, gates, todos",
            code="invalid_triage_focus",
        )
    if max_gates < 1 or max_todos < 1 or max_progress < 1 or max_text_chars < 32:
        raise StewardFastPathError(
            "triage bounds must be positive integers (max_text_chars >= 32)",
            code="invalid_triage_bounds",
        )

    scoped_goal_ids = _select_scoped_goals(
        authorized_goal_ids=authorized_goal_ids,
        requested_goal_ids=requested_goal_ids,
    )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_arg,
        registry_path=registry_path,
    )

    all_gates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    all_attention_todos: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    all_progress: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    unavailable_goals: list[dict[str, str]] = []
    unavailable_recent_progress: list[str] = []

    for goal_id in scoped_goal_ids:
        try:
            todo_snapshot = list_goal_todos(
                registry_path=registry_path,
                goal_id=goal_id,
                runtime_root_arg=runtime_root_arg,
            )
            todos = todo_snapshot.get("todos")
            if (todo_snapshot.get("ok") is not True or not isinstance(todos, list)
                    or todo_snapshot.get("todo_count", len(todos)) != len(todos)
                    or any(not isinstance(row, Mapping) or not row.get("todo_id") for row in todos)):
                raise ValueError("Goal Todo coverage is incomplete")
        except (
            CanonicalAuthorityRequired,
            LocalCoordinationAuthorityUnavailable,
            OSError,
            ValueError,
        ) as exc:
            error_code = str(getattr(exc, "code", "") or "goal_readback_unavailable")
            unavailable_goals.append({"goal_id": goal_id, "error_code": error_code})
            continue

        for index, raw_todo in enumerate(todos):
            if not isinstance(raw_todo, Mapping):
                continue
            todo_id = str(raw_todo.get("todo_id") or "").strip()
            if not todo_id or raw_todo.get("superseded_by"):
                continue
            role = str(raw_todo.get("role") or "").strip()
            status = str(raw_todo.get("status") or TODO_STATUS_OPEN).strip()
            done = bool(raw_todo.get("done")) or status == TODO_STATUS_DONE
            task_class = str(raw_todo.get("task_class") or "").strip()
            priority = _effective_priority(raw_todo)

            if (
                role == "user"
                and not done
                and status in ACTIVE_TRIAGE_STATUSES
                and task_class in ACTIONABLE_USER_TASK_CLASSES
            ):
                has_decision_scope = isinstance(raw_todo.get("decision_scope"), Mapping)
                scope_summary = _decision_scope_summary(raw_todo.get("decision_scope"))
                unblocks_todo_id = (
                    str(raw_todo.get("unblocks_todo_id") or "").strip() or None
                )
                gate_entry: dict[str, Any] = {
                    "goal_id": goal_id,
                    "todo_id": todo_id,
                    "task_class": task_class,
                    "status": status,
                    "priority": priority,
                    "global_gate": bool(raw_todo.get("global_gate")),
                    "text": _compact_public(
                        _strip_priority_prefix(raw_todo.get("text")), limit=max_text_chars
                    )
                    or WITHHELD_TEXT,
                    "note": _compact_public(
                        raw_todo.get("note") or raw_todo.get("reason"),
                        limit=max_text_chars,
                    )
                    or None,
                    "unblocks_todo_id": unblocks_todo_id,
                    "has_decision_scope": has_decision_scope,
                    "decision_scope_summary": scope_summary,
                    "state_fingerprint": compute_user_gate_state_fingerprint(
                        goal_id, raw_todo
                    ),
                }
                gate_sort = (
                    0 if gate_entry["global_gate"] else 1,
                    0 if task_class == TODO_TASK_CLASS_USER_GATE else 1,
                    _priority_rank(priority),
                    goal_id,
                    index,
                    todo_id,
                )
                all_gates.append((gate_sort, gate_entry))
                continue

            if role == "agent" and not done and status in ACTIVE_TRIAGE_STATUSES:
                is_blocked = status == TODO_STATUS_BLOCKED
                is_high_priority = priority in HIGH_PRIORITIES
                if is_blocked or is_high_priority:
                    todo_entry: dict[str, Any] = {
                        "goal_id": goal_id,
                        "todo_id": todo_id,
                        "status": status,
                        "priority": priority,
                        "task_class": task_class or None,
                        "claimed_by": (
                            str(raw_todo.get("claimed_by") or "").strip() or None
                        ),
                        "text": _compact_public(
                            _strip_priority_prefix(raw_todo.get("text")),
                            limit=max_text_chars,
                        )
                        or WITHHELD_TEXT,
                        "reason": _compact_public(
                            raw_todo.get("reason") or raw_todo.get("note"),
                            limit=max_text_chars,
                        )
                        or None,
                    }
                    todo_sort = (
                        _priority_rank(priority),
                        0 if is_blocked else 1,
                        goal_id,
                        index,
                        todo_id,
                    )
                    all_attention_todos.append((todo_sort, todo_entry))
                continue

            if focus == "all" and role == "agent" and done:
                completed_stamp = str(
                    raw_todo.get("completed_at") or raw_todo.get("updated_at") or ""
                ).strip()
                progress_entry: dict[str, Any] = {
                    "kind": "completed_todo",
                    "goal_id": goal_id,
                    "todo_id": todo_id,
                    "timestamp": completed_stamp or None,
                    "summary": _compact_public(
                        raw_todo.get("text"), limit=max_text_chars
                    ),
                }
                all_progress.append(
                    (
                        (*chronology_key(completed_stamp), index),
                        progress_entry,
                    )
                )

        runs_index = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
        if focus == "all":
            try:
                index_snapshot = load_index_snapshot(
                    runs_index, include_artifact_status=False, skip_invalid_encoding=True,
                )
            except OSError:
                unavailable_recent_progress.append(goal_id)
                continue
            if index_snapshot.invalid_count:
                unavailable_recent_progress.append(goal_id)
            for run_idx, run in enumerate(index_snapshot.records[-3:]):
                if not isinstance(run, Mapping):
                    continue
                summary = _compact_public(
                    run.get("summary") or run.get("headline") or "",
                    limit=max_text_chars,
                )
                if not summary:
                    continue
                generated_at = str(run.get("generated_at") or "").strip()
                all_progress.append(
                    (
                        (*chronology_key(generated_at), run_idx),
                        {
                            "kind": "run_summary",
                            "goal_id": goal_id,
                            "todo_id": (
                                str(run.get("todo_id") or "").strip() or None
                            ),
                            "timestamp": generated_at or None,
                            "summary": summary,
                        },
                    )
                )

    all_gates.sort(key=lambda item: item[0])
    all_attention_todos.sort(key=lambda item: item[0])
    all_progress.sort(key=lambda item: item[0], reverse=True)

    sorted_gates = [item[1] for item in all_gates]
    sorted_todos = [item[1] for item in all_attention_todos]
    sorted_progress = [item[1] for item in all_progress]

    visible_gates = (
        sorted_gates[:max_gates] if focus in {"all", "gates"} else []
    )
    visible_todos = (
        sorted_todos[:max_todos] if focus in {"all", "todos"} else []
    )
    visible_progress = (
        sorted_progress[:max_progress] if focus == "all" else []
    )

    blocked_count = sum(
        1 for item in sorted_todos if item.get("status") == TODO_STATUS_BLOCKED
    )
    high_priority_count = sum(
        1 for item in sorted_todos if item.get("priority") in HIGH_PRIORITIES
    )
    quiet = bool(scoped_goal_ids) and not (
        unavailable_goals or unavailable_recent_progress or sorted_gates or sorted_todos
    )

    return {
        "ok": True,
        "schema_version": PORTFOLIO_TRIAGE_SCHEMA_VERSION,
        "focus": focus,
        "quiet": quiet,
        "scoped_goal_ids": scoped_goal_ids,
        "counts": {
            "scoped_goals": len(scoped_goal_ids),
            "unavailable_goals": len(unavailable_goals),
            "open_user_gates": len(sorted_gates),
            "visible_user_gates": len(visible_gates),
            "omitted_user_gates": (
                max(0, len(sorted_gates) - len(visible_gates))
                if focus in {"all", "gates"}
                else len(sorted_gates)
            ),
            "attention_todos": len(sorted_todos),
            "blocked_todos": blocked_count,
            "high_priority_todos": high_priority_count,
            "visible_attention_todos": len(visible_todos),
            "omitted_attention_todos": (
                max(0, len(sorted_todos) - len(visible_todos))
                if focus in {"all", "todos"}
                else len(sorted_todos)
            ),
            "visible_recent_progress": len(visible_progress),
            "omitted_recent_progress": (
                max(0, len(sorted_progress) - len(visible_progress))
                if focus == "all"
                else len(sorted_progress)
            ),
        },
        "open_user_gates": visible_gates,
        "attention_todos": visible_todos,
        "recent_progress": visible_progress,
        "unavailable_goals": unavailable_goals,
        "unavailable_recent_progress": unavailable_recent_progress,
        "bounds": {
            "max_gates": max_gates,
            "max_todos": max_todos,
            "max_progress": max_progress,
            "max_text_chars": max_text_chars,
        },
        "execution_profile": {
            "fast_path": True,
            "read_only": True,
            "lease_acquired": False,
            "model_invoked": False,
            "history_compacted": False,
            "quota_spent": False,
            "external_writes_performed": False,
        },
    }


def render_portfolio_triage_markdown(projection: Mapping[str, Any]) -> str:
    """Render a concise mobile-friendly Markdown summary of portfolio triage."""

    counts = (
        projection.get("counts")
        if isinstance(projection.get("counts"), Mapping)
        else {}
    )
    gates = (
        projection.get("open_user_gates")
        if isinstance(projection.get("open_user_gates"), list)
        else []
    )
    todos = (
        projection.get("attention_todos")
        if isinstance(projection.get("attention_todos"), list)
        else []
    )
    progress = (
        projection.get("recent_progress")
        if isinstance(projection.get("recent_progress"), list)
        else []
    )
    unavailable = (
        projection.get("unavailable_goals")
        if isinstance(projection.get("unavailable_goals"), list)
        else []
    )

    lines = [
        "# Steward Fast-Path Triage",
        "",
        (
            f"- Scope: `{counts.get('scoped_goals', 0)}` goal(s) | "
            f"Open gates: `{counts.get('open_user_gates', 0)}` | "
            f"P0/P1 & blocked: `{counts.get('attention_todos', 0)}`"
        ),
    ]
    progress_unavailable = projection.get("unavailable_recent_progress") or []
    readable = counts.get("scoped_goals", 0) - len(unavailable)
    if unavailable:
        coverage = "Project status unavailable." if not readable else "Showing the projects whose status could be read."
        lines.extend(["", coverage])
    if progress_unavailable:
        lines.extend(["", f"Recent progress is incomplete for {len(progress_unavailable)} project(s)."])
    if projection.get("quiet") is True and readable and not unavailable and not progress_unavailable:
        lines.extend(
            [
                "",
                "All clear: no open user gates or P0/P1/blocked todos require attention.",
            ]
        )
    if gates:
        lines.extend(["", "## Open User Gates"])
        for gate in gates:
            if not isinstance(gate, Mapping):
                continue
            badge = f"[{gate['priority']}] " if gate.get("priority") else ""
            unblocks = (
                f" (unblocks `{gate['unblocks_todo_id']}`)"
                if gate.get("unblocks_todo_id")
                else ""
            )
            lines.append(
                f"- `{gate.get('goal_id')}` · `{gate.get('todo_id')}`: "
                f"{badge}{gate.get('text')}{unblocks}"
            )
        omitted_gates = int(counts.get("omitted_user_gates") or 0)
        if omitted_gates > 0 and projection.get("focus") in {"all", "gates"}:
            lines.append(f"- _+{omitted_gates} more open user gate(s) omitted_")

    if todos:
        lines.extend(["", "## P0/P1 & Blocked Todos"])
        for item in todos:
            if not isinstance(item, Mapping):
                continue
            tags = []
            if item.get("priority"):
                tags.append(str(item["priority"]))
            if item.get("status") == TODO_STATUS_BLOCKED:
                tags.append("blocked")
            prefix = f"[{'/'.join(tags)}] " if tags else ""
            lines.append(
                f"- `{item.get('goal_id')}` · `{item.get('todo_id')}`: "
                f"{prefix}{item.get('text')}"
            )
        omitted_todos = int(counts.get("omitted_attention_todos") or 0)
        if omitted_todos > 0 and projection.get("focus") in {"all", "todos"}:
            lines.append(f"- _+{omitted_todos} more attention todo(s) omitted_")

    if progress:
        lines.extend(["", "## Recent Progress"])
        for entry in progress:
            if not isinstance(entry, Mapping):
                continue
            todo_part = f" · `{entry['todo_id']}`" if entry.get("todo_id") else ""
            lines.append(
                f"- `{entry.get('goal_id')}`{todo_part}: {entry.get('summary')}"
            )

    if unavailable:
        lines.extend(["", "## Unavailable Goals"])
        for row in unavailable:
            if isinstance(row, Mapping):
                lines.append(
                    f"- `{row.get('goal_id')}`: `{row.get('error_code')}`"
                )

    return "\n".join(lines).rstrip() + "\n"


def execute_steward_fast_path(
    *,
    text: str,
    bindings: Any,
    binding_id: str,
    source_ref: str,
    sender_ref: str,
    registry_path: Path,
    runtime_root_arg: str | None = None,
    private_human_message: bool = True,
    origin: str | None = None,
    session_context: dict[str, Any] | None = None,
    max_gates: int = 8,
    max_todos: int = 8,
    max_progress: int = 5,
) -> dict[str, Any] | None:
    """Execute a read-only Steward Fast-Path request if `text` matches a fast intent.

    Returns `None` when `text` is not a fast-path triage command so the caller
    can proceed through ordinary Steward Turn admission unchanged. Raises
    `StewardFastPathError` fail-closed when `text` IS a fast-path command but
    the conversation binding, sender observation, or requested Goal scope is
    unauthorized.
    """

    intent = parse_steward_fast_path_intent(text)
    if intent is None:
        return None

    try:
        resolved = bindings.resolve(
            binding_id=binding_id,
            source_ref=source_ref,
            sender_ref=sender_ref,
            private_human_message=private_human_message,
            origin=origin,
            session_context=session_context,
        )
    except (KeyError, OSError, ValueError) as exc:
        raise StewardFastPathError(
            str(exc) or "steward conversation binding is not authorized",
            code="steward_binding_unauthorized",
        ) from exc

    binding = resolved.get("binding") if isinstance(resolved, Mapping) else None
    if (
        not isinstance(binding, Mapping)
        or binding.get("enabled") is not True
        or binding.get("context_kind") != "steward"
    ):
        raise StewardFastPathError(
            "fast-path triage requires an enabled Steward conversation binding",
            code="steward_binding_required",
        )

    projection = project_steward_triage(
        intent,
        resolved,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        max_gates=max_gates,
        max_todos=max_todos,
        max_progress=max_progress,
    )
    return {
        "ok": True,
        "schema_version": FAST_PATH_RESPONSE_SCHEMA_VERSION,
        "handled": True,
        "command": intent.command,
        "focus": intent.focus,
        "binding_id": str(binding.get("binding_id") or binding_id),
        "triage": projection,
        "markdown": render_portfolio_triage_markdown(projection),
    }


def project_steward_triage(
    intent: StewardFastPathIntent,
    resolved: Mapping[str, Any],
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    max_gates: int = 8,
    max_todos: int = 8,
    max_progress: int = 5,
) -> dict[str, Any]:
    """Read the portfolio using the result of a fresh Core binding resolution."""
    binding = resolved["binding"]
    context = resolved.get("context")
    steward_scope = (
        context.get("steward_scope") if isinstance(context, Mapping) else None
    )
    if isinstance(context, Mapping) and isinstance(context.get("goal_ids"), list):
        authorized_goal_ids = list(context["goal_ids"])
    elif isinstance(steward_scope, Mapping) and isinstance(
        steward_scope.get("goal_ids"), list
    ):
        authorized_goal_ids = [
            str(item) for item in steward_scope["goal_ids"] if str(item).strip()
        ]
    elif binding.get("goal_scope") == "all_registered":
        authorized_goal_ids = resolve_all_registered_goal_ids(registry_path)
    else:
        raw_ids = binding.get("goal_ids")
        authorized_goal_ids = (
            [str(item) for item in raw_ids if str(item).strip()]
            if isinstance(raw_ids, list)
            else []
        )

    return build_portfolio_triage_projection(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        authorized_goal_ids=authorized_goal_ids,
        requested_goal_ids=intent.requested_goal_ids or None,
        focus=intent.focus,
        max_gates=max_gates,
        max_todos=max_todos,
        max_progress=max_progress,
    )


__all__ = [
    "FAST_PATH_RESPONSE_SCHEMA_VERSION",
    "PORTFOLIO_TRIAGE_SCHEMA_VERSION",
    "StewardFastPathError",
    "StewardFastPathIntent",
    "build_portfolio_triage_projection",
    "compute_user_gate_state_fingerprint",
    "execute_steward_fast_path",
    "parse_steward_fast_path_intent",
    "project_steward_triage",
    "render_portfolio_triage_markdown",
    "resolve_all_registered_goal_ids",
]
