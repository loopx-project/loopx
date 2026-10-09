from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from .control_plane.todos.mutation_api import add_goal_todo
from .public_safe_text import LOCAL_PATH_SURFACE_PATTERN
from .control_plane.work_items.governed_transition_proposal import (
    STEWARD_TEAM_PLAN_PREVIEW_KIND,
)


CHAT_AGENT_RESPONSE_SCHEMA_VERSION = "loopx_chat_agent_response_v0"
CHAT_TODO_PREVIEW_SCHEMA_VERSION = "loopx_chat_todo_preview_v0"
CHAT_TODO_RECEIPT_SCHEMA_VERSION = "loopx_chat_todo_receipt_v0"
CHAT_TODO_NO_WRITE_RECEIPT_SCHEMA_VERSION = "loopx_chat_todo_no_write_receipt_v0"
CHAT_REVIEW_OPEN_TAG = "<loopx-review-json>"
CHAT_REVIEW_CLOSE_TAG = "</loopx-review-json>"
CHAT_TODO_ACTION_KIND = "loopx_chat_proposal"
CHAT_PROTECTED_ACTION_OPERATIONS = frozenset(
    {"merge", "release", "deploy", "delete", "payment"}
)

# Keep the existing action-admission contract separate from display redaction.
_ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:/(?:Users|home|private|tmp|var)/[^\s`'\"<>]+|[A-Za-z]:[\\/][^\s`'\"<>]+)"
)


def _protected_path_replacements(
    protected_paths: Iterable[Path | str],
) -> list[tuple[str, str]]:
    replacements = []
    for index, value in enumerate(protected_paths):
        raw = str(value).rstrip("/\\")
        if raw:
            label = "[project]" if index == 0 else "[local-path]"
            # Status redacts serialized JSON as well as ordinary response text.
            for spelling in {raw, json.dumps(raw, ensure_ascii=False)[1:-1]}:
                replacements.append((spelling, label))
    return sorted(replacements, key=lambda item: len(item[0]), reverse=True)


def _local_path_pattern(replacements: list[tuple[str, str]]) -> re.Pattern[str]:
    if not replacements:
        return LOCAL_PATH_SURFACE_PATTERN
    roots = "|".join(re.escape(raw) for raw, _ in replacements)
    return re.compile(
        r"(?<![:/A-Za-z0-9_.\\])(?:" + roots + r")"
        r"(?=$|[/\\\s`'\"<>.,;:!?)}\]])(?:[/\\][^\s`'\"<>]*)?"
        + "|(?i:" + LOCAL_PATH_SURFACE_PATTERN.pattern + ")"
    )


class TodoReviewPreviewConflict(ValueError):
    def __init__(self, message: str, *, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt


class ChatMessagePayloadConflictError(ValueError):
    def __init__(self, message_id: str, field: str) -> None:
        self.message_id = message_id
        self.field = field
        message = f"message_id conflicts with existing message payload field: {field}"
        super().__init__(message)


_MESSAGE_PAYLOAD_FIELDS = (
    "role",
    "text",
    "turn_id",
    "origin",
    "attachments",
    "goal_draft",
)


def require_matching_message_replay(
    existing: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    for field in _MESSAGE_PAYLOAD_FIELDS:
        existing_value = existing.get(field)
        candidate_value = candidate.get(field)
        if field == "attachments":
            existing_value = [] if existing_value is None else existing_value
            candidate_value = [] if candidate_value is None else candidate_value
        if existing_value != candidate_value:
            raise ChatMessagePayloadConflictError(
                str(candidate["message_id"]),
                field,
            )


def require_matching_replay(
    existing: Mapping[str, Any], *, identity: str, request: Mapping[str, Any]
) -> None:
    if any(existing.get(field) != value for field, value in request.items()):
        raise ValueError(f"{identity} already belongs to a different request")


def resolve_attached_completion_replay(
    messages: Iterable[Mapping[str, Any]],
    *,
    turn_id: str,
    completion_id: str,
    response_message: str,
) -> str | None:
    """Return the canonical message id to append, or None for a valid replay."""

    rows = list(messages)
    turn_rows = [
        row
        for row in rows
        if row.get("role") == "agent" and row.get("turn_id") == turn_id
    ]
    if len(turn_rows) > 1:
        raise ValueError("attached completion transcript identity is ambiguous")
    current_id = f"attached.{turn_id}.completed"
    if not turn_rows:
        if any(row.get("message_id") == current_id for row in rows):
            raise ValueError("attached completion transcript identity conflicts")
        return current_id
    row = turn_rows[0]
    if row.get("origin") != "attached_host" or row.get("message_id") not in {
        current_id,
        f"attached.{completion_id}",
    }:
        raise ValueError("attached completion transcript identity conflicts")
    if row.get("text") != response_message:
        raise ValueError("attached completion transcript conflicts with response")
    return None


def _stable_digest(payload: dict[str, Any], *, length: int = 24) -> str:
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:length]


def redact_local_paths(
    text: str,
    *,
    protected_paths: Iterable[Path | str] = (),
    project_relative: bool = False,
) -> str:
    """Hide local roots; visible answers may retain a project-relative filename.

    Structured status, proposals and gates keep the default full redaction.
    Additional protected roots always hide their descendants, even when nested
    inside the project. This is text presentation, not filesystem admission.
    """
    redacted = str(text or "")
    replacements = _protected_path_replacements(protected_paths)

    def path_parts(value: str) -> tuple[str, ...]:
        decoded = unquote(value).replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", decoded):
            decoded = decoded.casefold()
        return tuple(part for part in decoded.split("/") if part not in {"", "."})

    private_roots = [path_parts(raw) for raw, label in replacements if label == "[local-path]"]

    def replace_absolute_path(match: re.Match[str]) -> str:
        matched = match.group(0)
        candidate = matched.rstrip(".,;:!?)]}")
        suffix = matched[len(candidate) :]
        for raw, label in replacements:
            if candidate == raw or candidate.startswith(f"{raw}/") or candidate.startswith(f"{raw}\\"):
                if project_relative and label == "[project]" and candidate != raw:
                    relative = re.sub(r"\\+", "/", candidate[len(raw):]).lstrip("/")
                    components = unquote(relative).replace("\\", "/").split("/")
                    if ".." not in components:
                        # Equivalent spellings must not bypass a nested private
                        # root when opting into project-relative presentation.
                        candidate_parts = path_parts(candidate)
                        if any(candidate_parts[:len(root)] == root for root in private_roots):
                            return f"[local-path]{suffix}"
                        return f"./{relative}{suffix}"
                return f"{label}{suffix}"
        return f"[local-path]{suffix}"

    return _local_path_pattern(replacements).sub(replace_absolute_path, redacted)


def redact_response_markdown(text: str, *, protected_paths: Iterable[Path | str] = ()) -> str:
    """Keep local inline-link labels without publishing unusable destinations.

    This bounded display repair handles balanced inline links, not reference
    resolution or action admission. Code stays opaque to the link repair and
    all output still passes through the existing path privacy owner.
    """
    protected = tuple(protected_paths)
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in str(text or "").splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            lines.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            lines.append(line)
            continue
        edits: list[tuple[int, int, str]] = []
        cursor, ticks = 0, 0
        while cursor < len(line):
            if line[cursor] == "\\" and not ticks:
                cursor += 2
                continue
            if line[cursor] == "`":
                end = cursor + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                size = end - cursor
                ticks = size if not ticks else 0 if size == ticks else ticks
                cursor = end
                continue
            if ticks or line[cursor] != "[":
                cursor += 1
                continue
            start, end, depth = cursor, cursor + 1, 1
            while end < len(line) and depth:
                if line[end] == "\\":
                    end += 2
                    continue
                depth += (line[end] == "[") - (line[end] == "]")
                end += 1
            if depth or line[end:end + 1] != "(":
                cursor = end
                continue
            destination_start, close, depth = end + 1, end + 1, 1
            while close < len(line) and depth:
                if line[close] == "\\":
                    close += 2
                    continue
                depth += (line[close] == "(") - (line[close] == ")")
                close += 1
            if depth:
                cursor = close
                continue
            destination = line[destination_start:close - 1]
            decoded = unquote(destination)
            local = decoded.lstrip("< ").lower().startswith("file:") or any(
                redact_local_paths(value, protected_paths=protected) != value
                for value in (destination, decoded)
            )
            if local:
                image_start = start - 1 if start and line[start - 1] == "!" else start
                edits.append((image_start, close, line[start + 1:end - 1]))
            cursor = close
        cursor = 0
        chunks: list[str] = []
        for start, end, label in edits:
            chunks.extend((line[cursor:start], label))
            cursor = end
        chunks.append(line[cursor:])
        lines.append("".join(chunks))
    return redact_local_paths("".join(lines), protected_paths=protected, project_relative=True)


class VisibleResponseStreamFilter:
    """Stream safe operator text while withholding the structured review envelope."""

    _FLUSH_BOUNDARIES = {"\n", "。", "！", "？"}
    # Latin sentence punctuation ends a sentence only before whitespace, so
    # decimals, versions, file names and URLs never split. The split lands on
    # whitespace, which the length fallback below already treats as safe.
    _SPACED_SENTENCE_ENDINGS = {".", "!", "?"}
    _MAX_PENDING_CHARS = 160

    def __init__(self, *, protected_paths: Iterable[Path | str] = ()) -> None:
        self.protected_paths = tuple(protected_paths)
        self._protected = _protected_path_replacements(self.protected_paths)
        self._local_path_pattern = _local_path_pattern(self._protected)
        self.marker_pending = ""
        self.visible_pending = ""
        self.envelope_started = False

    def _next_boundary(self, pending: str) -> int:
        boundary = -1
        search_limit = min(len(pending), self._MAX_PENDING_CHARS)
        for index, character in enumerate(pending[:search_limit]):
            if character in self._FLUSH_BOUNDARIES:
                boundary = index + 1
            elif (
                character in self._SPACED_SENTENCE_ENDINGS
                and pending[index + 1 : index + 2] in {" ", "\t"}
            ):
                boundary = index + 2
        if boundary < 0 and len(pending) >= self._MAX_PENDING_CHARS:
            prefix = pending[: self._MAX_PENDING_CHARS + 1]
            paths = list(self._local_path_pattern.finditer(pending))
            whitespace = max(
                (index for index, character in enumerate(prefix)
                 if character in " \t"
                 and not any(match.start() <= index < match.end() for match in paths)),
                default=-1,
            )
            # A declared root itself can span several chunks or contain spaces.
            # Hold its prefix until it becomes a complete path token.
            partial_root = any(raw.startswith(pending) for raw, _ in self._protected)
            if whitespace >= 0:
                boundary = whitespace + 1
            elif partial_root:
                return -1
            elif not any(match.start() < self._MAX_PENDING_CHARS for match in paths):
                boundary = self._MAX_PENDING_CHARS
            else:
                for index, character in enumerate(
                    pending[self._MAX_PENDING_CHARS :],
                    start=self._MAX_PENDING_CHARS,
                ):
                    if character in " \t\r\n`'\"<>":
                        boundary = index + 1
                        break
        return boundary

    def _accept_visible(self, text: str, *, final: bool) -> str:
        self.visible_pending += text
        if final:
            ready = self.visible_pending
            self.visible_pending = ""
            return redact_local_paths(ready, protected_paths=self.protected_paths, project_relative=True)
        # One chunk can hold several safe boundaries. Keep cutting until none
        # is left, so an early sentence never holds back a long tail that the
        # length fallback would otherwise release.
        ready_length = 0
        while (boundary := self._next_boundary(self.visible_pending[ready_length:])) > 0:
            ready_length += boundary
        if not ready_length:
            return ""
        ready = self.visible_pending[:ready_length]
        self.visible_pending = self.visible_pending[ready_length:]
        return redact_local_paths(ready, protected_paths=self.protected_paths, project_relative=True)

    def feed(self, chunk: str) -> str:
        if self.envelope_started:
            return ""
        self.marker_pending += str(chunk or "")
        marker_at = self.marker_pending.find(CHAT_REVIEW_OPEN_TAG)
        if marker_at >= 0:
            visible = self.marker_pending[:marker_at]
            self.marker_pending = ""
            self.envelope_started = True
            return self._accept_visible(visible, final=True)
        suffix_length = 0
        limit = min(len(self.marker_pending), len(CHAT_REVIEW_OPEN_TAG) - 1)
        for length in range(1, limit + 1):
            if self.marker_pending.endswith(CHAT_REVIEW_OPEN_TAG[:length]):
                suffix_length = length
        if suffix_length:
            visible = self.marker_pending[:-suffix_length]
            self.marker_pending = self.marker_pending[-suffix_length:]
        else:
            visible = self.marker_pending
            self.marker_pending = ""
        return self._accept_visible(visible, final=False)

    def finish(self) -> str:
        if not self.envelope_started:
            self.visible_pending += self.marker_pending
        self.marker_pending = ""
        return self._accept_visible("", final=True)


def _compact_line(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def _normalize_proposals(
    value: Any,
    *,
    protected_paths: Iterable[Path | str],
    team_plan_context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return proposals
    for raw in value[:5]:
        if not isinstance(raw, dict):
            continue
        if raw.get("kind") == STEWARD_TEAM_PLAN_PREVIEW_KIND:
            # A team plan is admitted here or not at all: without the host facts
            # that say which Agents and action kinds exist, a preview cannot be
            # validated, so it is never surfaced half-checked.
            preview = _validated_team_plan_preview(raw, team_plan_context)
            if preview is not None:
                proposals.append(
                    {"kind": STEWARD_TEAM_PLAN_PREVIEW_KIND, "preview": preview}
                )
            continue
        if raw.get("kind") != "todo":
            continue
        text = _compact_line(redact_local_paths(str(raw.get("text") or ""), protected_paths=protected_paths), limit=400)
        if not text:
            continue
        priority = str(raw.get("priority") or "P1").upper()
        if priority not in {"P0", "P1", "P2"}:
            priority = "P1"
        rationale = _compact_line(
            redact_local_paths(str(raw.get("rationale") or ""), protected_paths=protected_paths),
            limit=500,
        )
        proposals.append(
            {
                "kind": "todo",
                "text": text,
                "priority": priority,
                "rationale": rationale,
            }
        )
    return proposals


def _validated_team_plan_preview(
    raw: Mapping[str, Any], context: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Return the validated preview, or ``None`` when it may not be surfaced.

    A preview names its Goal, so the host facts are per Goal rather than for
    "the" Goal: the manager channel is not bound to one, and a plan for a Goal
    the host was not given facts for is dropped instead of being validated
    against another Goal's Agents.
    """

    if not isinstance(context, Mapping):
        return None
    from .control_plane.work_items.governed_transition_proposal import (
        validate_steward_team_plan_preview,
    )

    goal_id = str(raw.get("goal_id") or "")
    agents: list[str] | None = None
    by_goal = context.get("registered_agents_by_goal")
    if isinstance(by_goal, Mapping):
        declared = by_goal.get(goal_id)
        if isinstance(declared, (list, tuple)):
            agents = [str(value) for value in declared]
    if agents is None:
        # A host with a large Goal set resolves on demand, and only for the
        # Goal the plan named, so admission stays bounded by one lookup.
        resolve = context.get("resolve_registered_agents")
        if callable(resolve):
            try:
                resolved = resolve(goal_id)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                resolved = None
            if isinstance(resolved, (list, tuple)):
                agents = [str(value) for value in resolved]
    if agents is None:
        return None
    try:
        return validate_steward_team_plan_preview(
            raw,
            registered_agent_ids=agents,
            supported_action_kinds=list(context.get("supported_action_kinds") or []),
        )
    except ValueError:
        # A malformed preview is dropped exactly like any other proposal this
        # normalizer cannot accept; the answer text still reaches the owner.
        return None


def _normalize_protected_action(
    value: Any,
    *,
    protected_paths: Iterable[Path | str],
) -> dict[str, str] | None:
    """Accept a narrow semantic proposal without granting execution authority."""

    if not isinstance(value, dict):
        return None
    operation = _compact_line(value.get("operation"), limit=40).lower()
    if operation not in CHAT_PROTECTED_ACTION_OPERATIONS:
        return None
    raw_target = _compact_line(value.get("target"), limit=200)
    if not raw_target or _ABSOLUTE_LOCAL_PATH.search(raw_target):
        return None
    target = _compact_line(
        redact_local_paths(raw_target, protected_paths=protected_paths),
        limit=160,
    )
    if not target or target != raw_target[:160].strip():
        return None
    summary = _compact_line(
        redact_local_paths(
            str(value.get("summary") or ""),
            protected_paths=protected_paths,
        ),
        limit=300,
    )
    return {
        "operation": operation,
        "target": target,
        "summary": summary,
    }


def _normalize_gate(value: Any, *, protected_paths: Iterable[Path | str]) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    kind = _compact_line(value.get("kind"), limit=80)
    summary = _compact_line(
        redact_local_paths(
            str(value.get("summary") or value.get("message") or ""),
            protected_paths=protected_paths,
        ),
        limit=500,
    )
    next_action = _compact_line(
        redact_local_paths(
            str(value.get("next_action") or value.get("required_action") or ""),
            protected_paths=protected_paths,
        ),
        limit=500,
    )
    if not kind and not summary:
        return None
    return {
        "kind": kind or "host_gate",
        "summary": summary,
        "next_action": next_action,
    }


def _normalize_goal_draft(payload: Mapping[str, Any], *, protected_paths: Iterable[Path | str]) -> dict[str, Any] | None:
    if payload.get("goal_draft") is None:
        return None
    from .control_plane.effect_runtime import effect_runtime_result

    draft = effect_runtime_result("collaboration.goal_draft", dict(payload)).get("draft")
    if not draft:
        return None
    # Python owns transport redaction; the shared TypeScript owner admits structure.
    return {
        key: [redact_local_paths(option, protected_paths=protected_paths) for option in field]
        if isinstance(field, list) else redact_local_paths(field, protected_paths=protected_paths)
        for key, field in draft.items()
    }


def normalize_agent_response(
    payload: Mapping[str, Any],
    *,
    protected_paths: Iterable[Path | str] = (),
    team_plan_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one structured provider response to the public Chat contract."""

    from .capabilities.manager_context import normalize_request
    handoff = normalize_request(payload.get("context_handoff"))
    protected = tuple(protected_paths)
    message = redact_response_markdown(
        str(payload.get("message") or ""),
        protected_paths=protected,
    ).strip()
    goal_draft = _normalize_goal_draft(payload, protected_paths=protected)
    return {
        "schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION,
        "message": message,
        **({"goal_draft": goal_draft} if goal_draft else {}),
        **({"context_handoff": handoff} if handoff else {}),
        "proposals": _normalize_proposals(
            payload.get("proposals"),
            protected_paths=protected,
            team_plan_context=team_plan_context,
        ),
        "protected_action": _normalize_protected_action(
            payload.get("protected_action"),
            protected_paths=protected,
        ),
        "gate": _normalize_gate(payload.get("gate"), protected_paths=protected),
    }


def parse_agent_response(
    raw_text: str,
    *,
    protected_paths: Iterable[Path | str] = (),
    team_plan_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    protected = tuple(protected_paths)
    start = raw_text.rfind(CHAT_REVIEW_OPEN_TAG)
    end = raw_text.rfind(CHAT_REVIEW_CLOSE_TAG)
    if start >= 0 and end > start:
        body = raw_text[start + len(CHAT_REVIEW_OPEN_TAG) : end].strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return normalize_agent_response(
                payload,
                protected_paths=protected,
                team_plan_context=team_plan_context,
            )
        key = re.search(r'"message"\s*:\s*', body)
        if key:
            try:
                salvaged, _ = json.JSONDecoder().raw_decode(body[key.end() :])
            except json.JSONDecodeError:
                salvaged = None
            if isinstance(salvaged, str) and salvaged.strip():
                return {
                    "schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION,
                    "message": redact_response_markdown(salvaged, protected_paths=protected).strip(),
                    "proposals": [],
                    "protected_action": None,
                    "gate": None,
                }
        raw_text = raw_text[:start]
    elif start >= 0:
        # The review envelope is an authority boundary, not display text.  A
        # provider can finish after emitting a complete JSON object but before
        # emitting the closing tag.  In that case retain only a human-readable
        # answer and fail closed for proposals, gates, and protected actions.
        # Never leak the raw protocol fragment into downstream transports.
        visible = raw_text[:start].strip()
        body = raw_text[start + len(CHAT_REVIEW_OPEN_TAG) :].strip()
        salvaged_message = ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            salvaged_message = str(payload.get("message") or "").strip()
        else:
            key = re.search(r'"message"\s*:\s*', body)
            if key:
                try:
                    salvaged, _ = json.JSONDecoder().raw_decode(body[key.end() :])
                except json.JSONDecodeError:
                    salvaged = None
                if isinstance(salvaged, str):
                    salvaged_message = salvaged.strip()
        raw_text = visible or salvaged_message
    return {
        "schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION,
        "message": redact_response_markdown(raw_text, protected_paths=protected).strip(),
        "proposals": [],
        "protected_action": None,
        "gate": None,
    }


def _normalize_todo_text(text: str) -> str:
    normalized = _compact_line(text, limit=400)
    if not normalized:
        raise ValueError("todo text is required")
    if _ABSOLUTE_LOCAL_PATH.search(normalized):
        raise ValueError("todo text must not contain a local absolute path")
    return normalized


def _todo_revision(payload: dict[str, Any]) -> str | None:
    # Canonical dry-runs return their provider head; the compatibility writer
    # retains its existing document revision witness.
    if payload.get("decision_read_from_provider") is True:
        return str(payload["provider_revision"])
    correctness = payload.get("local_state_write_correctness")
    if not isinstance(correctness, dict):
        return None
    intent = correctness.get("write_intent")
    if not isinstance(intent, dict):
        return None
    expected = intent.get("expected_revision")
    if not isinstance(expected, dict):
        return None
    value = expected.get("value")
    return str(value) if value else None


def _compact_todo_payload(payload: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    canonical = payload.get("todo")
    record = canonical if isinstance(canonical, dict) else {}
    todo = {
        "goal_id": str(payload.get("goal_id") or ""),
        "todo_id": str(payload.get("todo_id") or ""),
        "text": str(record.get("text") if record else payload.get("todo") or ""),
        "status": str(record.get("status") if record else payload.get("status") or "open"),
        "task_class": str(record.get("task_class") or payload.get("task_class") or "advancement_task"),
        "action_kind": str(record.get("action_kind") or payload.get("action_kind") or CHAT_TODO_ACTION_KIND),
    }
    return {
        "ok": bool(payload.get("ok")),
        "schema_version": CHAT_TODO_PREVIEW_SCHEMA_VERSION,
        "dry_run": not applied,
        "applied": applied,
        "added": bool(payload.get("added")),
        "already_exists": bool(payload.get("already_exists")),
        "todo": todo,
    }


def _todo_preview_fingerprint(payload: dict[str, Any]) -> str:
    compact = _compact_todo_payload(payload, applied=False)
    if payload.get("decision_read_from_provider") is True:
        # A create dry-run allocates a proposed operation identity without a
        # write. Its random Todo id is not the reviewed intent or source head.
        compact["todo"].pop("todo_id")
    return _stable_digest(
        {
            "schema_version": CHAT_TODO_PREVIEW_SCHEMA_VERSION,
            "todo": compact["todo"],
            "added": compact["added"],
            "already_exists": compact["already_exists"],
            "expected_revision": _todo_revision(payload),
        }
    )


def _todo_write_receipt(
    *,
    preview_id: str,
    preview_payload: dict[str, Any],
    applied_payload: dict[str, Any],
) -> dict[str, Any]:
    compact = _compact_todo_payload(applied_payload, applied=True)
    todo = compact["todo"]
    outcome = "todo_already_exists" if compact["already_exists"] else "todo_added"
    receipt = {
        "schema_version": CHAT_TODO_RECEIPT_SCHEMA_VERSION,
        "preview_id": preview_id,
        "goal_id": todo["goal_id"],
        "todo_id": todo["todo_id"],
        "status": "applied",
        "outcome": outcome,
        "already_exists": compact["already_exists"],
        "preview_revision": _todo_revision(preview_payload),
    }
    receipt["receipt_id"] = _stable_digest(receipt)
    return receipt


def _todo_no_write_receipt(
    *,
    goal_id: str,
    current_preview: dict[str, Any],
) -> dict[str, Any]:
    receipt = {
        "schema_version": CHAT_TODO_NO_WRITE_RECEIPT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "status": "not_applied",
        "outcome": "preview_stale",
        "write_attempted": False,
        "current_preview_id": _todo_preview_fingerprint(current_preview),
        "state_revision": _todo_revision(current_preview),
    }
    receipt["receipt_id"] = _stable_digest(receipt)
    return receipt


def _add_review_todo(
    *,
    registry_path: Path,
    goal_id: str,
    text: str,
    priority: str | None = None,
    dry_run: bool,
) -> dict[str, Any]:
    return add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
        text=_normalize_todo_text(text),
        priority=priority,
        task_class="advancement_task",
        action_kind=CHAT_TODO_ACTION_KIND,
        dry_run=dry_run,
    )


def build_todo_review_preview(
    *,
    registry_path: Path,
    goal_id: str,
    text: str,
    priority: str | None = None,
) -> dict[str, Any]:
    payload = _add_review_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        text=text,
        priority=priority,
        dry_run=True,
    )
    compact = _compact_todo_payload(payload, applied=False)
    compact["preview_id"] = _todo_preview_fingerprint(payload)
    return compact


def apply_todo_review_preview(
    *,
    registry_path: Path,
    goal_id: str,
    text: str,
    priority: str | None = None,
    preview_id: str,
) -> dict[str, Any]:
    current_preview = _add_review_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        text=text,
        priority=priority,
        dry_run=True,
    )
    if not preview_id or preview_id != _todo_preview_fingerprint(current_preview):
        raise TodoReviewPreviewConflict(
            "stale todo preview; preview the proposal again before applying",
            receipt=_todo_no_write_receipt(
                goal_id=goal_id,
                current_preview=current_preview,
            ),
        )
    applied = _add_review_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        text=text,
        priority=priority,
        dry_run=False,
    )
    compact = _compact_todo_payload(applied, applied=True)
    compact["receipt"] = _todo_write_receipt(
        preview_id=preview_id,
        preview_payload=current_preview,
        applied_payload=applied,
    )
    return compact
