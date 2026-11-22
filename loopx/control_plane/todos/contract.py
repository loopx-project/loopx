from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
import re
from dataclasses import dataclass
from enum import Enum
from typing import TypedDict
from urllib.parse import quote, unquote

from ...repository_identity import normalize_repository_identity
from .completion_state import (
    normalize_todo_completion_continuation,
    normalize_todo_completion_recovery,
    normalize_todo_no_followup as normalize_todo_no_followup,
    require_todo_completion_metadata,
)
from .resume_condition import (
    TODO_RESUME_KIND_CAPACITY_AVAILABLE as TODO_RESUME_KIND_CAPACITY_AVAILABLE,
    TODO_RESUME_KIND_MONITOR_CHANGED as TODO_RESUME_KIND_MONITOR_CHANGED,
    TODO_RESUME_KIND_PR_MERGED as TODO_RESUME_KIND_PR_MERGED,
    TODO_RESUME_KIND_RESUME_AT as TODO_RESUME_KIND_RESUME_AT,
    TODO_RESUME_KIND_TODO_DONE as TODO_RESUME_KIND_TODO_DONE,
    TODO_RESUME_KIND_VALUES as TODO_RESUME_KIND_VALUES,
    normalize_supported_todo_resume_when as normalize_supported_todo_resume_when,
    normalize_todo_generation as normalize_todo_generation,
    normalize_todo_resume_when as normalize_todo_resume_when,
    require_supported_todo_resume_when as require_supported_todo_resume_when,
)


_READ_LEGACY_CODEC = json.loads(Path(__file__).with_name("read_legacy_codec.json").read_text(encoding="utf-8"))

TODO_TASK_PATTERN = re.compile(r"^\s*[-*]\s+\[([ xX-])\]\s+(.+?)\s*$")
TODO_METADATA_PATTERN = re.compile(r"^\s*<!--\s*loopx:(?:todo\s+)?(?P<body>.*?)\s*-->\s*$")
TODO_METADATA_TOKEN_PATTERN = re.compile(
    r"(?<!\S)(?P<key>[a-z_][a-z0-9_]*)=(?P<value>[^\s<>]+)"
)
TODO_ACTION_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TODO_TASK_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
TODO_ID_PATTERN = re.compile(r"^todo_[a-z0-9_-]{3,64}$")
TODO_AGENT_CLAIM_PATTERN = re.compile(r"^[a-z][a-z0-9_.:@-]{0,79}$")
TODO_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
TODO_CAPABILITY_BINDING_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9_.-]{0,31}:[a-z][a-z0-9_.:-]{2,95}$"
)
TODO_REPLAN_OBLIGATION_ID_PATTERN = re.compile(r"^replan-[a-f0-9]{16}$")
TODO_EXPLORE_RESULT_NODE_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")
TODO_DECISION_SCOPE_KEY_PATTERN = re.compile(r"^(?:\*|[a-z0-9][a-z0-9_.:@*/-]{0,95})$")
TODO_WRITE_SCOPE_MAX_CHARS = 160
TODO_MONITOR_METADATA_FIELDS = (
    "target_key",
    "monitor_effect_id",
    "cadence",
    "next_due_at",
    "expires_at",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "material_change_scope",
    "material_change_scope_generations",
    "max_no_change_before_replan",
    "watch_only",
)


def normalize_todo_watch_only(value: object) -> bool | None:
    if value is True or str(value or "").strip().lower() == "true":
        return True
    if value is False or str(value or "").strip().lower() == "false":
        return False
    return None

TODO_TASK_CLASS_ADVANCEMENT = "advancement_task"
TODO_TASK_CLASS_MONITOR = "continuous_monitor"
TODO_TASK_CLASS_USER_GATE = "user_gate"
TODO_TASK_CLASS_USER_ACTION = "user_action"
TODO_TASK_CLASS_BLOCKER = "blocker"
TODO_TASK_CLASS_VALUES = {
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    TODO_TASK_CLASS_USER_GATE,
    TODO_TASK_CLASS_USER_ACTION,
    TODO_TASK_CLASS_BLOCKER,
}


def resolve_next_user_task_class(
    next_user_todo: str | None,
    next_user_task_class: str | None,
) -> str | None:
    if not next_user_todo:
        if next_user_task_class:
            raise ValueError("--next-user-task-class requires --next-user-todo")
        return None
    if not next_user_task_class:
        raise ValueError(
            "--next-user-todo requires explicit --next-user-task-class "
            "user_action|user_gate"
        )
    if next_user_task_class not in {
        TODO_TASK_CLASS_USER_GATE,
        TODO_TASK_CLASS_USER_ACTION,
    }:
        raise ValueError(
            "next_user_task_class must be one of: user_action, user_gate"
        )
    return next_user_task_class


TODO_DECISION_SCOPE_SCHEMA_VERSION = "decision_scope_v0"
TODO_DECISION_SCOPE_OUTCOME_SCHEMA_VERSION = "todo_decision_scope_outcome_v0"
TODO_DECISION_OUTCOME_VALUES = {"approve", "reject", "cancel"}
TODO_DECISION_SCOPE_KIND_VALUES = {
    "private_read",
    "write_scope",
    "resource",
    "production",
    "public_claim",
    "direction",
    "other",
}
TODO_DECISION_SCOPE_GRANULARITY_VALUES = {
    "action",
    "lane",
    "goal",
    "project",
    "global",
}

TODO_STATUS_OPEN = "open"
TODO_STATUS_DONE = "done"
TODO_STATUS_BLOCKED = "blocked"
TODO_STATUS_DEFERRED = "deferred"
TODO_STATUS_VALUES = {
    TODO_STATUS_OPEN,
    TODO_STATUS_DONE,
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
}
TODO_TERMINAL_STATUS_VALUES = {TODO_STATUS_DONE, TODO_STATUS_DEFERRED}
TODO_LEGACY_TERMINAL_STATUS_VALUES = {"completed", "closed", "archived"}

TODO_ACTION_KIND_ADVANCEMENT_VALUES = set(_READ_LEGACY_CODEC["advancement_actions"])
TODO_ACTION_KIND_MONITOR_VALUES = set(_READ_LEGACY_CODEC["monitor_actions"])


class TodoContinuationPolicy(str, Enum):
    INDEPENDENT_HANDOFF = "independent_handoff"
    SAME_AGENT_NON_DELIVERY = "same_agent_non_delivery"


TODO_CONTINUATION_POLICY_VALUES = {
    policy.value for policy in TodoContinuationPolicy
}
TODO_REMOVED_REVIEW_CONTINUATION_POLICY_VALUES = {
    "primary_review",
    "review_handoff",
}


TODO_HARD_MONITOR_PATTERNS = tuple(re.compile(pattern) for pattern in _READ_LEGACY_CODEC["TODO_HARD_MONITOR_PATTERNS"])

TODO_ADVANCEMENT_OVERRIDE_PATTERNS = tuple(re.compile(pattern) for pattern in _READ_LEGACY_CODEC["TODO_ADVANCEMENT_OVERRIDE_PATTERNS"])
TODO_BLOCKED_MONITOR_PATTERNS = tuple(re.compile(pattern) for pattern in _READ_LEGACY_CODEC["TODO_BLOCKED_MONITOR_PATTERNS"])
TODO_MONITOR_PATTERNS = tuple(re.compile(pattern) for pattern in _READ_LEGACY_CODEC["TODO_MONITOR_PATTERNS"])
TODO_ADVANCEMENT_PATTERNS = tuple(re.compile(pattern) for pattern in _READ_LEGACY_CODEC["TODO_ADVANCEMENT_PATTERNS"])

NEXT_ACTION_HARD_MONITOR_PATTERNS = (
    re.compile(r"(?i)\bdo not\b.*\b(?:launch|run|execute|start)\b.*\buntil\b"),
    re.compile(r"(?i)\b(?:waiting|blocked|gated)\b.*\b(?:owner|user|credential|approval|prerequisite|evidence)\b"),
)
NEXT_ACTION_ADVANCEMENT_HINT_PATTERNS = (
    re.compile(r"(?i)\bplanning/self[- ]?repair\b"),
    re.compile(r"(?i)\bplanning[- ]?self[- ]?repair\b"),
    re.compile(r"(?i)\bself[- ]?repair capability\b"),
    re.compile(r"(?i)\badvance(?:ment)?[- ]class\b"),
    re.compile(r"(?i)\badvance primary backlog\b"),
    re.compile(r"(?i)\bnext eligible advancement turn\b"),
    re.compile(r"(?i)\bpackage\b.*\b(?:adapter|contract|artifact)\b"),
    re.compile(r"(?i)\bselect\b.*\b(?:task|validation hypothesis|validation step)\b"),
    re.compile(r"(?i)\b(?:local-material-ready|material-ready)\b.*\b(?:task|run|validation)\b"),
    re.compile(r"(?i)\b(?:run|test)\b.*\bvalidation hypothesis\b"),
    re.compile(
        r"(?i)\b(?:collect|aggregate|generate|produce|materialize)\b.*\b(?:then|and)\b.*\b"
        r"(?:run|rerun|repeat|rebuild|score|scorer|gate|eval|evaluate|validate)\b"
    ),
    re.compile(
        r"(?i)(?:^|[.;:：]\s*)(?:run|execute|test|validate|rebuild|compare|implement|fix|"
        r"write|package|collect|aggregate|generate|produce|materialize|rerun|repeat|"
        r"eval|evaluate|score)\b"
    ),
)


def compact_todo_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _match_token(
    value: object,
    pattern: re.Pattern[str],
    *,
    lower: bool = True,
    full: bool = False,
) -> str | None:
    candidate = str(value or "").strip()
    if lower:
        candidate = candidate.lower()
    if not candidate:
        return None
    matched = pattern.fullmatch(candidate) if full else pattern.match(candidate)
    return candidate if matched else None


def _choice_token(value: object, choices: set[str] | frozenset[str]) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in choices else None


def _split_delimited_items(value: object, *, split_regex: bool = True) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if split_regex:
        return list(re.split(r"[,;|]", str(value or "")))
    if isinstance(value, str):
        return list(value.split(","))
    return [value]


def _dedupe_normalized(
    raw_items: Iterable[object],
    normalize: Callable[[object], str | None],
) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for raw in raw_items:
        item = normalize(raw)
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items


def normalize_todo_action_kind(value: object) -> str | None:
    return _match_token(value, TODO_ACTION_KIND_PATTERN)


def normalize_todo_task_domain(value: object) -> str | None:
    return _match_token(value, TODO_TASK_DOMAIN_PATTERN, full=True)


def normalize_todo_replan_obligation_id(value: object) -> str | None:
    return _match_token(value, TODO_REPLAN_OBLIGATION_ID_PATTERN, lower=False, full=True)


def normalize_todo_capability_binding_ref(value: object) -> str | None:
    return _match_token(value, TODO_CAPABILITY_BINDING_REF_PATTERN, full=True)


def normalize_todo_task_repository(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return normalize_repository_identity(candidate)
    except ValueError:
        return None


def normalize_todo_continuation_policy(value: object) -> str | None:
    return _choice_token(value, TODO_CONTINUATION_POLICY_VALUES)


def normalize_removed_todo_continuation_policy(value: object) -> str | None:
    return _choice_token(value, TODO_REMOVED_REVIEW_CONTINUATION_POLICY_VALUES)


def normalize_todo_claimed_by(value: object) -> str | None:
    candidate = compact_todo_text(value).lower().replace(" ", "-")
    return candidate if candidate and TODO_AGENT_CLAIM_PATTERN.match(candidate) else None


def normalize_todo_blocks_agent(value: object) -> str | None:
    return normalize_todo_claimed_by(value)


def normalize_todo_bound_agent(value: object) -> str | None:
    return normalize_todo_claimed_by(value)


def normalize_todo_goal_bound(value: object) -> bool | None:
    return normalize_todo_global_gate(value)


def _normalize_todo_excluded_agents(
    value: object,
    *,
    strict: bool,
    field: str = "excluded_agents",
) -> list[str]:
    normalized: set[str] = set()
    for candidate in _split_delimited_items(value, split_regex=False):
        agent_id = normalize_todo_claimed_by(candidate)
        if not agent_id:
            if strict:
                raise ValueError(
                    f"{field} must contain public-safe agent tokens such as codex-side-bypass"
                )
            continue
        normalized.add(agent_id)
    return sorted(normalized)


def normalize_todo_excluded_agents(value: object) -> list[str]:
    return _normalize_todo_excluded_agents(value, strict=False)


def require_todo_excluded_agents(
    value: object,
    *,
    field: str = "excluded_agents",
) -> list[str]:
    return _normalize_todo_excluded_agents(value, strict=True, field=field)


def normalize_todo_global_gate(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    candidate = compact_todo_text(value).lower()
    if candidate in {"1", "true", "yes", "y", "global_gate", "global-gate"}:
        return True
    if candidate in {"0", "false", "no", "n"}:
        return False
    return None


def normalize_todo_id(value: object) -> str | None:
    return _match_token(value, TODO_ID_PATTERN)


def normalize_todo_id_list(value: object) -> list[str]:
    if value is None:
        return []
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    todo_ids: list[str] = []
    for raw_value in raw_values:
        for match in re.findall(r"\btodo_[A-Za-z0-9_-]+\b", str(raw_value or "")):
            todo_id = normalize_todo_id(match)
            if todo_id and todo_id not in todo_ids:
                todo_ids.append(todo_id)
        todo_id = normalize_todo_id(raw_value)
        if todo_id and todo_id not in todo_ids:
            todo_ids.append(todo_id)
    return todo_ids


def merge_todo_id_lists(*values: object) -> list[str]:
    todo_ids: list[str] = []
    for value in values:
        for todo_id in normalize_todo_id_list(value):
            if todo_id not in todo_ids:
                todo_ids.append(todo_id)
    return todo_ids


def _normalize_single_write_scope(raw: object) -> str | None:
    scope = compact_todo_text(raw)
    if (
        not scope
        or len(scope) > TODO_WRITE_SCOPE_MAX_CHARS
        or scope.startswith(("/", "~"))
        or ".." in scope.split("/")
        or re.search(r"[\s<>]", scope)
    ):
        return None
    return scope


def _normalize_single_capability(raw: object) -> str | None:
    capability = compact_todo_text(raw).lower().replace("-", "_").replace(" ", "_")
    return capability if capability and TODO_CAPABILITY_PATTERN.match(capability) else None


def _normalize_single_explore_ref(raw: object) -> str | None:
    ref = compact_todo_text(raw)
    return ref if ref and TODO_EXPLORE_RESULT_NODE_REF_PATTERN.match(ref) else None


def normalize_required_write_scopes(value: object) -> list[str]:
    return _dedupe_normalized(_split_delimited_items(value), _normalize_single_write_scope)


def normalize_required_capabilities(value: object) -> list[str]:
    return _dedupe_normalized(_split_delimited_items(value), _normalize_single_capability)


def normalize_target_capabilities(value: object) -> list[str]:
    return normalize_required_capabilities(value)


def normalize_explore_result_node_refs(value: object) -> list[str]:
    """Normalize explicit public-safe Explore node ids attached to a todo."""

    return _dedupe_normalized(_split_delimited_items(value), _normalize_single_explore_ref)


class ReplanSuccessorSemanticBinding(TypedDict, total=False):
    action_kind: str
    target_key: str
    explore_result_node_refs: list[str]


def replan_successor_semantic_binding(
    *,
    action_kind: object,
    target_key: object,
    explore_result_node_refs: object,
) -> ReplanSuccessorSemanticBinding | None:
    """Return the typed executable identity of a replan successor.

    An obligation id establishes causality, but it does not turn a planning
    instruction into executable work.  A successor also needs an explicit
    action and at least one stable target identity.
    """

    safe_action_kind = normalize_todo_action_kind(action_kind)
    safe_target_key = compact_todo_text(target_key) or None
    safe_explore_refs = normalize_explore_result_node_refs(
        explore_result_node_refs
    )
    if not safe_action_kind or not (safe_target_key or safe_explore_refs):
        return None
    binding: ReplanSuccessorSemanticBinding = {"action_kind": safe_action_kind}
    if safe_target_key:
        binding["target_key"] = safe_target_key
    if safe_explore_refs:
        binding["explore_result_node_refs"] = safe_explore_refs
    return binding


def _format_scope_identity(scope: Mapping[str, object]) -> str:
    return f"{scope['kind']}:{scope['granularity']}:{scope['scope_key']}"


def normalize_todo_decision_scope(value: object) -> dict[str, str] | None:
    """Normalize the compact decision-scope metadata token.

    Markdown todo metadata stores the v0 shape as
    ``kind:granularity:scope_key`` so authors do not need to embed JSON in
    active-state comments. Status and quota expose the normalized object.
    """

    raw_kind: object = None
    raw_granularity: object = None
    raw_scope_key: object = None
    raw_decision_id: object = None
    if isinstance(value, dict):
        raw_kind = value.get("kind")
        raw_granularity = value.get("granularity")
        raw_scope_key = value.get("scope_key")
        raw_decision_id = value.get("decision_id")
    else:
        candidate = compact_todo_text(value).lower()
        parts = candidate.split(":", 2)
        if len(parts) != 3:
            return None
        raw_kind, raw_granularity, raw_scope_key = parts

    kind = compact_todo_text(raw_kind).lower()
    granularity = compact_todo_text(raw_granularity).lower()
    scope_key = compact_todo_text(raw_scope_key).lower()
    if kind not in TODO_DECISION_SCOPE_KIND_VALUES:
        return None
    if granularity not in TODO_DECISION_SCOPE_GRANULARITY_VALUES:
        return None
    if not TODO_DECISION_SCOPE_KEY_PATTERN.match(scope_key):
        return None

    payload: dict[str, str] = {
        "schema_version": TODO_DECISION_SCOPE_SCHEMA_VERSION,
        "kind": kind,
        "granularity": granularity,
        "scope_key": scope_key,
    }
    decision_id = normalize_todo_id(raw_decision_id)
    if decision_id:
        payload["decision_id"] = decision_id
    return payload


def normalize_todo_decision_outcome(value: object) -> str | None:
    return _choice_token(value, TODO_DECISION_OUTCOME_VALUES)


def require_todo_decision_outcome(value: object) -> str:
    outcome = normalize_todo_decision_outcome(value)
    if not outcome:
        raise ValueError(
            "decision_outcome must be one of: "
            + ", ".join(sorted(TODO_DECISION_OUTCOME_VALUES))
        )
    return outcome


def require_todo_decision_scope(value: object) -> dict[str, str]:
    scope = normalize_todo_decision_scope(value)
    if not scope:
        raise ValueError(
            "decision_scope must use kind:granularity:scope_key, such as "
            "private_read:project:authority"
        )
    return scope


def _normalize_todo_required_decision_scopes(
    value: object,
    *,
    strict: bool,
) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in _split_delimited_items(value):
        scope = normalize_todo_decision_scope(raw)
        if not scope:
            if strict:
                raise ValueError(
                    "required_decision_scopes must contain "
                    "kind:granularity:scope_key tokens"
                )
            continue
        identity = (scope["kind"], scope["granularity"], scope["scope_key"])
        if identity in seen:
            continue
        seen.add(identity)
        scopes.append(scope)
    return scopes


def normalize_todo_required_decision_scopes(value: object) -> list[dict[str, str]]:
    return _normalize_todo_required_decision_scopes(value, strict=False)


def require_todo_required_decision_scopes(value: object) -> list[dict[str, str]]:
    return _normalize_todo_required_decision_scopes(value, strict=True)


def decision_scope_metadata_value(value: object) -> str | None:
    return None if value is None else _format_scope_identity(require_todo_decision_scope(value))


def required_decision_scopes_metadata_value(value: object) -> str | None:
    if value is None:
        return None
    scopes = require_todo_required_decision_scopes(value)
    return ",".join(_format_scope_identity(scope) for scope in scopes) or None


def normalize_todo_decision_scope_outcomes(value: object) -> list[dict[str, object]]:
    raw_values = _split_delimited_items(value, split_regex=False)
    outcomes: list[dict[str, object]] = []
    positions: dict[tuple[str, str, str], int] = {}
    for raw in raw_values:
        raw_outcome: object = None
        raw_scope: object = None
        raw_source_todo_id: object = None
        if isinstance(raw, dict):
            raw_outcome = raw.get("outcome")
            raw_scope = raw.get("decision_scope") or raw.get("scope")
            raw_source_todo_id = raw.get("source_todo_id")
        else:
            parts = str(raw or "").split("|", 2)
            if len(parts) == 3:
                raw_outcome, raw_scope, raw_source_todo_id = parts
        outcome = normalize_todo_decision_outcome(raw_outcome)
        scope = normalize_todo_decision_scope(raw_scope)
        source_todo_id = normalize_todo_id(raw_source_todo_id)
        if not outcome or not scope or not source_todo_id:
            continue
        payload: dict[str, object] = {
            "schema_version": TODO_DECISION_SCOPE_OUTCOME_SCHEMA_VERSION,
            "outcome": outcome,
            "decision_scope": scope,
            "source_todo_id": source_todo_id,
        }
        identity = (scope["kind"], scope["granularity"], scope["scope_key"])
        if identity in positions:
            outcomes[positions[identity]] = payload
        else:
            positions[identity] = len(outcomes)
            outcomes.append(payload)
    return outcomes


def _format_decision_scope_outcome_token(item: Mapping[str, object]) -> str:
    raw_scope = item.get("decision_scope")
    scope = raw_scope if isinstance(raw_scope, Mapping) else {}
    return f"{item.get('outcome', '')}|{_format_scope_identity(scope)}|{item.get('source_todo_id', '')}"


def decision_scope_outcomes_metadata_value(value: object) -> str | None:
    outcomes = normalize_todo_decision_scope_outcomes(value)
    if not outcomes:
        return None
    return ",".join(_format_decision_scope_outcome_token(item) for item in outcomes)


def build_todo_id(
    *,
    role: object,
    source_section: object,
    index: object,
    text: object,
) -> str:
    identity = "|".join(str(part or "") for part in (role, source_section, index, compact_todo_text(text)))
    return f"todo_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:12]}"


def normalize_explicit_todo_task_class(value: object) -> str | None:
    return _choice_token(value, TODO_TASK_CLASS_VALUES)


def normalize_todo_status(value: object) -> str | None:
    return _choice_token(value, TODO_STATUS_VALUES)


def normalize_todo_role(value: object) -> str | None:
    return _choice_token(value, {"user", "agent"})


def todo_done_for_status(status: object) -> bool:
    return normalize_todo_status(status) in TODO_TERMINAL_STATUS_VALUES


def todo_terminal_for_status(status: object) -> bool:
    candidate = str(status or "").strip().lower()
    return (
        normalize_todo_status(candidate) in TODO_TERMINAL_STATUS_VALUES
        or candidate in TODO_LEGACY_TERMINAL_STATUS_VALUES
    )


def todo_status_from_marker(marker: object) -> str:
    candidate = str(marker or "").strip().lower()
    if candidate == "x":
        return TODO_STATUS_DONE
    if candidate == "-":
        return TODO_STATUS_DEFERRED
    return TODO_STATUS_OPEN


def todo_marker_for_status(status: object) -> str:
    normalized = normalize_todo_status(status) or TODO_STATUS_OPEN
    if normalized == TODO_STATUS_DEFERRED:
        return "-"
    if normalized == TODO_STATUS_DONE:
        return "x"
    return " "


def encode_metadata_value(value: object, *, preserve_whitespace: bool = False) -> str:
    text = str(value or "") if preserve_whitespace else compact_todo_text(value)
    return quote(text, safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")


def decode_metadata_value(value: object, *, preserve_whitespace: bool = False) -> str:
    text = unquote(str(value or ""))
    return text if preserve_whitespace else compact_todo_text(text)


MetadataValueNormalizer = Callable[[object], object]
MetadataValueEncoder = Callable[[object], str | None]
MetadataInvalidPredicate = Callable[[object], bool]


def _truthy_value(value: object) -> object:
    return value if value else None


def _nonempty_metadata_text(value: object) -> str | None:
    return compact_todo_text(value) or None


def _nonblank_verbatim_metadata_text(value: object) -> str | None:
    if not isinstance(value, str):
        text = str(value or "")
        return text if text.strip() else None
    return value if value.strip() else None


def _optional_output_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _metadata_bool_text(value: object) -> str:
    return "true" if value else "false"


def _metadata_csv(value: object) -> str:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return ",".join(str(item) for item in value)
    return str(value or "")


def _normalize_optional_decision_scope_for_write(value: object) -> dict[str, str] | None:
    return None if value is None else require_todo_decision_scope(value)


def _normalize_optional_required_decision_scopes_for_write(
    value: object,
) -> list[dict[str, str]]:
    return [] if value is None else require_todo_required_decision_scopes(value)


@dataclass(frozen=True)
class _TodoMetadataField:
    key: str
    read_normalizer: MetadataValueNormalizer
    write_normalizer: MetadataValueNormalizer | None = None
    encoder: MetadataValueEncoder = str
    invalid_when: MetadataInvalidPredicate = bool
    invalid_message: str | None = None
    read_fallback_key: str | None = None
    read_fallback_normalizer: MetadataValueNormalizer | None = None
    block_field: bool = True
    preserve_whitespace: bool = False

    def normalize_for_write(self, value: object) -> object:
        normalizer = self.write_normalizer or self.read_normalizer
        normalized = normalizer(value)
        if (
            self.invalid_message
            and self.invalid_when(value)
            and not _metadata_value_is_present(normalized)
        ):
            raise ValueError(self.invalid_message)
        return normalized


def _metadata_value_is_present(value: object) -> bool:
    return value is not None and value != "" and value != []


def _bool_field(key: str, read_normalizer: MetadataValueNormalizer) -> _TodoMetadataField:
    return _TodoMetadataField(
        key,
        read_normalizer,
        write_normalizer=_optional_output_bool,
        encoder=_metadata_bool_text,
        invalid_when=lambda value: value is not None,
    )


def _todo_id_message(key: str) -> str:
    return f"{key} must use the public token shape todo_<letters-digits-underscore-hyphen>"


def _csv_field(
    key: str,
    read_normalizer: MetadataValueNormalizer,
    *,
    write_normalizer: MetadataValueNormalizer | None = None,
    invalid_message: str | None = None,
) -> _TodoMetadataField:
    return _TodoMetadataField(
        key,
        read_normalizer,
        write_normalizer=write_normalizer,
        encoder=_metadata_csv,
        invalid_message=invalid_message,
    )


_TODO_METADATA_FIELD_SCHEMA = (
    _TodoMetadataField("todo_id", normalize_todo_id, invalid_message=_todo_id_message("todo_id")),
    _TodoMetadataField(
        "status", normalize_todo_status,
        invalid_message="todo status must be one of: " + ", ".join(sorted(TODO_STATUS_VALUES)),
    ),
    _TodoMetadataField("role", normalize_todo_role, invalid_message="todo role must be one of: agent, user"),
    _TodoMetadataField(
        "task_class", normalize_explicit_todo_task_class,
        invalid_message="todo task_class must be one of: " + ", ".join(sorted(TODO_TASK_CLASS_VALUES)),
    ),
    _TodoMetadataField(
        "action_kind", normalize_todo_action_kind,
        invalid_message="todo action_kind must be a public-safe token: lowercase letters, digits, '_' or '-'",
    ),
    _TodoMetadataField(
        "task_domain", normalize_todo_task_domain,
        invalid_message="todo task_domain must be a public-safe token: lowercase letters, digits, '_', '-' or '.'",
    ),
    _TodoMetadataField(
        "capability_binding_ref", normalize_todo_capability_binding_ref,
        invalid_message="capability_binding_ref must be a public-safe namespaced token",
    ),
    _TodoMetadataField(
        "task_repository", normalize_todo_task_repository,
        invalid_message="todo task_repository must be a credential-free Git remote or canonical git:<host>/<path> identity",
    ),
    _TodoMetadataField(
        "continuation_policy", normalize_todo_continuation_policy,
        invalid_message="todo continuation_policy must be one of: " + ", ".join(sorted(TODO_CONTINUATION_POLICY_VALUES)),
        read_fallback_key="removed_continuation_policy",
        read_fallback_normalizer=normalize_removed_todo_continuation_policy,
    ),
    _TodoMetadataField(
        "removed_continuation_policy", normalize_removed_todo_continuation_policy,
        invalid_message=(
            "removed_continuation_policy must be one of: "
            + ", ".join(sorted(TODO_REMOVED_REVIEW_CONTINUATION_POLICY_VALUES))
        ),
        block_field=False,
    ),
    _csv_field(
        "required_write_scopes", normalize_required_write_scopes,
        invalid_message="required_write_scopes must contain public-safe relative scope tokens",
    ),
    _csv_field(
        "required_capabilities", normalize_required_capabilities,
        invalid_message="required_capabilities must contain public-safe capability tokens",
    ),
    _csv_field(
        "target_capabilities", normalize_target_capabilities,
        invalid_message="target_capabilities must contain public-safe capability tokens",
    ),
    _csv_field(
        "explore_result_node_refs", normalize_explore_result_node_refs,
        invalid_message="explore_result_node_refs must contain public-safe Explore node ids",
    ),
    _TodoMetadataField(
        "decision_scope", normalize_todo_decision_scope,
        write_normalizer=_normalize_optional_decision_scope_for_write,
        encoder=decision_scope_metadata_value,
        invalid_when=lambda value: value is not None,
    ),
    _TodoMetadataField(
        "required_decision_scopes", normalize_todo_required_decision_scopes,
        write_normalizer=_normalize_optional_required_decision_scopes_for_write,
        encoder=required_decision_scopes_metadata_value,
    ),
    _TodoMetadataField(
        "decision_outcome", normalize_todo_decision_outcome,
        invalid_message="decision_outcome must be one of: " + ", ".join(sorted(TODO_DECISION_OUTCOME_VALUES)),
    ),
    _TodoMetadataField(
        "decision_scope_outcomes", normalize_todo_decision_scope_outcomes,
        encoder=decision_scope_outcomes_metadata_value,
        invalid_message="decision_scope_outcomes must contain outcome, decision_scope, and source_todo_id",
    ),
    _TodoMetadataField(
        "claimed_by", normalize_todo_claimed_by,
        invalid_message="claimed_by must be a public-safe agent token such as codex-main-control",
    ),
    _TodoMetadataField(
        "last_actor_agent_id", normalize_todo_claimed_by,
        invalid_message="last_actor_agent_id must be a public-safe agent token",
    ),
    _TodoMetadataField(
        "bound_agent", normalize_todo_bound_agent,
        invalid_message="bound_agent must be a public-safe agent token such as codex-main-control",
    ),
    _bool_field("goal_bound", normalize_todo_goal_bound),
    _TodoMetadataField(
        "blocks_agent", normalize_todo_blocks_agent,
        invalid_message="blocks_agent must be a public-safe agent token such as codex-side-bypass",
    ),
    _csv_field("excluded_agents", normalize_todo_excluded_agents, write_normalizer=require_todo_excluded_agents),
    _bool_field("global_gate", normalize_todo_global_gate),
    _TodoMetadataField("unblocks_todo_id", normalize_todo_id, invalid_message=_todo_id_message("unblocks_todo_id")),
    _csv_field(
        "successor_todo_ids", normalize_todo_id_list,
        invalid_message="successor_todo_ids must contain public todo_<letters-digits-underscore-hyphen> tokens",
    ),
    _TodoMetadataField(
        "completion_continuation", normalize_todo_completion_continuation,
        invalid_message="completion_continuation must be one of: active_goal, successor, no_followup",
    ),
    _TodoMetadataField(
        "completion_recovery", normalize_todo_completion_recovery,
        invalid_message="completion_recovery must be same_turn_terminal_closeout or lifecycle_reentry_terminal_closeout",
    ),
    _TodoMetadataField(
        "replan_obligation_id", normalize_todo_replan_obligation_id,
        invalid_message="replan_obligation_id must use the current replan-<16 lowercase hex> identity",
    ),
    _TodoMetadataField(
        "resume_when", normalize_todo_resume_when,
        invalid_message=(
            "resume_when must be public-safe, e.g. todo_done:todo_ab12cd34ef56, "
            "monitor_changed:todo_monitor123, pr_merged:#532, or capacity_available:short_pool"
        ),
    ),
    _TodoMetadataField(
        "resume_monitor_generation", normalize_todo_generation,
        invalid_message="resume_monitor_generation must be a non-negative integer",
    ),
    _bool_field("no_followup", normalize_todo_no_followup),
    *(
        _TodoMetadataField(
            key, _nonempty_metadata_text,
            write_normalizer=normalize_todo_generation if key == "material_change_generation" else _truthy_value,
        )
        for key in TODO_MONITOR_METADATA_FIELDS
    ),
    *(
        _TodoMetadataField(key, _nonempty_metadata_text, write_normalizer=_truthy_value)
        for key in ("note", "evidence", "reason", "completed_at", "updated_at", "completion_turn_key", "completion_receipt_id")
    ),
    # The declaration digest includes exact command arguments and label text.
    # Percent encoding keeps these values within one Markdown comment line.
    *(
        _TodoMetadataField(key, _nonblank_verbatim_metadata_text, write_normalizer=_truthy_value, preserve_whitespace=True)
        for key in ("validation_command", "validation_command_argv")
    ),
    _TodoMetadataField("validation_label", _truthy_value, preserve_whitespace=True),
    _TodoMetadataField("validation_timeout_seconds", _nonempty_metadata_text, write_normalizer=_truthy_value),
    _TodoMetadataField("superseded_by", normalize_todo_id, invalid_message=_todo_id_message("superseded_by")),
)

_TODO_METADATA_FIELD_BY_TOKEN = {
    field.key: field for field in _TODO_METADATA_FIELD_SCHEMA
}


def parse_todo_metadata_tokens(line: str) -> list[tuple[str, str]] | None:
    match = TODO_METADATA_PATTERN.match(line)
    if not match:
        return None
    tokens = []
    for token in TODO_METADATA_TOKEN_PATTERN.finditer(match.group("body")):
        key = token.group("key")
        field = _TODO_METADATA_FIELD_BY_TOKEN.get(key)
        value = decode_metadata_value(
            token.group("value"),
            preserve_whitespace=field is not None and field.preserve_whitespace,
        )
        tokens.append((key, value))
    return tokens


def _todo_metadata_block_fields() -> tuple[str, ...]:
    fields = [
        field.key for field in _TODO_METADATA_FIELD_SCHEMA if field.block_field
    ]
    repository_index = fields.index("task_repository")
    continuation_index = fields.index("continuation_policy")
    fields[repository_index], fields[continuation_index] = (
        fields[continuation_index],
        fields[repository_index],
    )
    return tuple(fields)


TODO_METADATA_FIELDS = _todo_metadata_block_fields()


def parse_todo_metadata_line(line: str) -> dict[str, object] | None:
    tokens = parse_todo_metadata_tokens(line)
    if tokens is None:
        return None
    metadata: dict[str, object] = {}
    for key, value in tokens:
        field = _TODO_METADATA_FIELD_BY_TOKEN.get(key)
        if field is None:
            continue
        normalized = field.read_normalizer(value)
        output_key = field.key
        if (
            not _metadata_value_is_present(normalized)
            and field.read_fallback_key
            and field.read_fallback_normalizer
        ):
            normalized = field.read_fallback_normalizer(value)
            output_key = field.read_fallback_key
        if _metadata_value_is_present(normalized):
            metadata[output_key] = normalized
    return metadata or None


def _normalize_todo_metadata_for_write(values: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for field in _TODO_METADATA_FIELD_SCHEMA:
        value = field.normalize_for_write(values.get(field.key))
        if _metadata_value_is_present(value):
            normalized[field.key] = value
        if field.key == "removed_continuation_policy" and {
            "continuation_policy",
            "removed_continuation_policy",
        }.issubset(normalized):
            raise ValueError(
                "continuation_policy and removed_continuation_policy are mutually exclusive"
            )
        if field.key == "excluded_agents":
            claimed_by = normalized.get("claimed_by")
            excluded = normalized.get("excluded_agents")
            if (
                isinstance(claimed_by, str)
                and claimed_by
                and isinstance(excluded, list)
                and claimed_by in excluded
            ):
                raise ValueError(
                    f"claimed_by={claimed_by!r} cannot also appear in excluded_agents"
                )
    return normalized


def normalize_todo_metadata_for_write(values: Mapping[str, object]) -> dict[str, object]:
    """Return canonical typed Todo metadata without Markdown encoding."""

    return _normalize_todo_metadata_for_write(values)


def _format_todo_metadata_values(values: Mapping[str, object]) -> str | None:
    normalized = _normalize_todo_metadata_for_write(values)
    fields = []
    for field in _TODO_METADATA_FIELD_SCHEMA:
        if field.key in normalized:
            value = encode_metadata_value(
                field.encoder(normalized[field.key]),
                preserve_whitespace=field.preserve_whitespace,
            )
            fields.append(f"{field.key}={value}")
    if not fields:
        return None
    return f"  <!-- loopx:todo {' '.join(fields)} -->"


def format_todo_metadata_line(
    *,
    todo_id: str | None = None,
    status: str | None = None,
    role: str | None = None,
    task_class: str | None = None,
    action_kind: str | None = None,
    task_domain: str | None = None,
    capability_binding_ref: str | None = None,
    task_repository: str | None = None,
    continuation_policy: str | None = None,
    removed_continuation_policy: str | None = None,
    required_write_scopes: object = None,
    required_capabilities: object = None,
    target_capabilities: object = None,
    explore_result_node_refs: object = None,
    decision_scope: object = None,
    required_decision_scopes: object = None,
    decision_outcome: str | None = None,
    decision_scope_outcomes: object = None,
    claimed_by: str | None = None,
    last_actor_agent_id: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool | None = None,
    blocks_agent: str | None = None,
    excluded_agents: object = None,
    global_gate: bool | None = None,
    unblocks_todo_id: str | None = None,
    successor_todo_ids: object = None,
    completion_continuation: str | None = None,
    completion_recovery: str | None = None,
    completion_receipt_id: str | None = None,
    replan_obligation_id: str | None = None,
    resume_when: str | None = None,
    resume_monitor_generation: int | str | None = None,
    no_followup: bool | None = None,
    target_key: str | None = None,
    monitor_effect_id: str | None = None,
    cadence: str | None = None,
    next_due_at: str | None = None,
    expires_at: str | None = None,
    last_checked_at: str | None = None,
    result_hash: str | None = None,
    consecutive_no_change: str | None = None,
    material_change: str | None = None,
    material_change_generation: int | str | None = None,
    material_change_scope: str | None = None,
    material_change_scope_generations: str | None = None,
    max_no_change_before_replan: str | None = None,
    watch_only: str | None = None,
    note: str | None = None,
    evidence: str | None = None,
    completion_turn_key: str | None = None,
    validation_command: str | None = None,
    validation_command_argv: str | None = None,
    validation_label: str | None = None,
    validation_timeout_seconds: str | None = None,
    reason: str | None = None,
    completed_at: str | None = None,
    updated_at: str | None = None,
    superseded_by: str | None = None,
) -> str | None:
    values = locals()
    return _format_todo_metadata_values(values)


_BLOCK_READ_NORMALIZERS: dict[str, MetadataValueNormalizer] = {
    "required_write_scopes": normalize_required_write_scopes,
    "task_domain": normalize_todo_task_domain,
    "task_repository": normalize_todo_task_repository,
    "required_capabilities": normalize_required_capabilities,
    "target_capabilities": normalize_target_capabilities,
    "excluded_agents": normalize_todo_excluded_agents,
    "explore_result_node_refs": normalize_explore_result_node_refs,
    "continuation_policy": normalize_todo_continuation_policy,
    "decision_scope": normalize_todo_decision_scope,
    "required_decision_scopes": normalize_todo_required_decision_scopes,
    "decision_outcome": normalize_todo_decision_outcome,
    "decision_scope_outcomes": normalize_todo_decision_scope_outcomes,
    "no_followup": normalize_todo_no_followup,
    "completion_continuation": normalize_todo_completion_continuation,
    "completion_recovery": normalize_todo_completion_recovery,
    "global_gate": normalize_todo_global_gate,
    "goal_bound": normalize_todo_global_gate,
}

_BLOCK_TRUTHY_OR_POP_NORMALIZERS: dict[str, MetadataValueNormalizer] = {
    "required_write_scopes": normalize_required_write_scopes,
    "task_repository": normalize_todo_task_repository,
    "required_capabilities": normalize_required_capabilities,
    "target_capabilities": normalize_target_capabilities,
    "excluded_agents": normalize_todo_excluded_agents,
    "explore_result_node_refs": normalize_explore_result_node_refs,
    "required_decision_scopes": require_todo_required_decision_scopes,
    "decision_scope_outcomes": normalize_todo_decision_scope_outcomes,
    "successor_todo_ids": normalize_todo_id_list,
}

_BLOCK_OPTIONAL_BOOL_NORMALIZERS: dict[str, Callable[[object], bool | None]] = {
    "no_followup": normalize_todo_no_followup,
    "global_gate": normalize_todo_global_gate,
    "goal_bound": normalize_todo_global_gate,
}

_BLOCK_STRICT_SETTERS: dict[str, MetadataValueNormalizer] = {
    "task_domain": _TODO_METADATA_FIELD_BY_TOKEN["task_domain"].normalize_for_write,
    "decision_scope": require_todo_decision_scope,
    "decision_outcome": require_todo_decision_outcome,
}


def todo_block_metadata(block: Mapping[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in TODO_METADATA_FIELDS:
        value = block.get(key)
        if value is None:
            continue
        normalizer = _BLOCK_READ_NORMALIZERS.get(key)
        if normalizer is not None:
            normalized = normalizer(value)
        elif _TODO_METADATA_FIELD_BY_TOKEN[key].preserve_whitespace:
            normalized = value
        else:
            normalized = str(value).strip()
        if _metadata_value_is_present(normalized):
            metadata[key] = normalized
    return metadata


def _apply_todo_block_update(
    metadata: dict[str, object],
    key: str,
    value: object,
) -> None:
    if value is None:
        metadata.pop(key, None)
    elif (truthy_norm := _BLOCK_TRUTHY_OR_POP_NORMALIZERS.get(key)) is not None:
        normalized = truthy_norm(value)
        if normalized:
            metadata[key] = normalized
        else:
            metadata.pop(key, None)
    elif (strict_setter := _BLOCK_STRICT_SETTERS.get(key)) is not None:
        metadata[key] = strict_setter(value)
    elif key == "continuation_policy":
        normalized_policy = normalize_todo_continuation_policy(value)
        if normalized_policy:
            metadata[key] = normalized_policy
        elif value:
            raise ValueError(
                "todo continuation_policy must be one of: "
                + ", ".join(sorted(TODO_CONTINUATION_POLICY_VALUES))
            )
        else:
            metadata.pop(key, None)
    elif (bool_norm := _BLOCK_OPTIONAL_BOOL_NORMALIZERS.get(key)) is not None:
        normalized_bool = bool_norm(value)
        if normalized_bool is not None:
            metadata[key] = normalized_bool
        else:
            metadata.pop(key, None)
    elif key in {"completion_continuation", "completion_recovery"}:
        normalized_completion = require_todo_completion_metadata(key, value)
        if normalized_completion:
            metadata[key] = normalized_completion
        else:
            metadata.pop(key, None)
    elif _TODO_METADATA_FIELD_BY_TOKEN[key].preserve_whitespace:
        metadata[key] = value
    elif str(value).strip():
        metadata[key] = str(value).strip()


def metadata_line_for_todo_block(
    block: Mapping[str, object],
    updates: Mapping[str, object],
) -> str | None:
    metadata = todo_block_metadata(block)
    for key, value in updates.items():
        if key in TODO_METADATA_FIELDS:
            _apply_todo_block_update(metadata, key, value)
    if "todo_id" not in metadata and block.get("todo_id"):
        metadata["todo_id"] = str(block["todo_id"])
    if "status" not in metadata:
        metadata["status"] = TODO_STATUS_DONE if block.get("done") else TODO_STATUS_OPEN
    return _format_todo_metadata_values(metadata)


_TODO_TEXT_CLASS_RULES: tuple[tuple[tuple[re.Pattern[str], ...], str], ...] = (
    (TODO_HARD_MONITOR_PATTERNS, TODO_TASK_CLASS_MONITOR),
    (TODO_ADVANCEMENT_OVERRIDE_PATTERNS, TODO_TASK_CLASS_ADVANCEMENT),
    (TODO_BLOCKED_MONITOR_PATTERNS, TODO_TASK_CLASS_MONITOR),
    (TODO_MONITOR_PATTERNS, TODO_TASK_CLASS_MONITOR),
    (TODO_ADVANCEMENT_PATTERNS, TODO_TASK_CLASS_ADVANCEMENT),
)


def todo_task_class_for_text(text: str) -> str:
    compact = compact_todo_text(text)
    for patterns, task_class in _TODO_TEXT_CLASS_RULES:
        if any(pattern.search(compact) for pattern in patterns):
            return task_class
    return TODO_TASK_CLASS_ADVANCEMENT


def normalize_todo_task_class(value: object, *, text: str, action_kind: object = None) -> str:
    candidate = normalize_explicit_todo_task_class(value)
    if candidate:
        return candidate
    normalized_action_kind = normalize_todo_action_kind(action_kind)
    if normalized_action_kind in TODO_ACTION_KIND_ADVANCEMENT_VALUES:
        return TODO_TASK_CLASS_ADVANCEMENT
    if normalized_action_kind in TODO_ACTION_KIND_MONITOR_VALUES:
        return TODO_TASK_CLASS_MONITOR
    return todo_task_class_for_text(text)


def next_action_requires_advancement_text(text: str) -> bool:
    compact = compact_todo_text(text)
    if not compact:
        return False
    if any(pattern.search(compact) for pattern in NEXT_ACTION_HARD_MONITOR_PATTERNS):
        return False
    return any(pattern.search(compact) for pattern in NEXT_ACTION_ADVANCEMENT_HINT_PATTERNS)
