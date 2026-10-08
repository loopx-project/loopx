from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
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
TODO_EXPLORE_RESULT_NODE_REF_LIMIT = 8
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
    "max_no_change_before_replan",
    "watch_only",
)


def normalize_todo_watch_only(value: Any) -> bool | None:
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

TODO_ACTION_KIND_ADVANCEMENT_VALUES = {
    "advance",
    "analyze",
    "benchmark_run",
    "codex_run",
    "compact_blocker_writeback",
    "compare",
    "execute",
    "fix",
    "implement",
    "rebuild",
    "rebuild_score",
    "repair",
    "run",
    "run_eval",
    "test",
    "validate",
    "writeback",
}
TODO_ACTION_KIND_MONITOR_VALUES = {
    "external_evidence",
    "monitor",
    "observe",
    "poll",
    "watch",
}


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


TODO_HARD_MONITOR_PATTERNS = (
    re.compile(r"(?i)\bdo not\b.*\b(?:launch|run|execute|start)\b.*\buntil\b"),
    re.compile(r"(?i)\b(?:only|just)\b.*\b(?:after|when|once)\b.*\b(?:owner|user|credential|approval|prerequisite|evidence)\b"),
    re.compile(r"(?i)\b(?:credential|gcp|gcs|gcp_project|gcp_sa_key|gs://)\b.*\b(?:missing|required|provide|proof|prerequisite|gate|gated)\b"),
    re.compile(r"(?i)\b(?:readiness|proof)\b.*\bbefore any formal\b.*\brun\b"),
    re.compile(r"(?i)\bremaining formal\b.*\bpath\b"),
    re.compile(r"(?i)\b(?:route|input)\b.*\babsent\b"),
    re.compile(r"(?i)\b0\b.*\b(?:candidate|candidates)\b"),
)

TODO_ADVANCEMENT_OVERRIDE_PATTERNS = (
    re.compile(
        r"(?i)(?:^|[:：]\s*)(?:implement|add|make|fix|build|wire|define|compare|run|"
        r"execute|test|validate|rebuild|repair|archive|publish|merge|write|attribute|"
        r"collect|aggregate|generate|produce|materialize|rerun|repeat|eval|evaluate|score)\b"
    ),
    re.compile(
        r"(?i)\b(?:implementation slice|validation-backed patch|smoke fixture|"
        r"regression suite|readiness scan|source preflight|setup-readiness scan)\b"
    ),
)
TODO_BLOCKED_MONITOR_PATTERNS = (
    *TODO_HARD_MONITOR_PATTERNS,
    re.compile(r"(?i)\b(?:blocked|gated|waiting)\b.*\b(?:owner|user|credential|substrate|proof|prerequisite|evidence)\b"),
)
TODO_MONITOR_PATTERNS = (
    re.compile(r"(?i)\bdependency monitor\b"),
    re.compile(r"(?i)\bobservation lane\b"),
    re.compile(r"(?i)(?:^|[:：]\s*)observe\b"),
    re.compile(r"(?i)(?:^|[:：]\s*)poll\b"),
    re.compile(r"(?i)(?:^|[:：]\s*)watch\b"),
    re.compile(r"(?i)\bmonitor-only\b"),
    *TODO_BLOCKED_MONITOR_PATTERNS,
)
TODO_ADVANCEMENT_PATTERNS = (
    *TODO_ADVANCEMENT_OVERRIDE_PATTERNS,
    re.compile(r"(?i)\b(?:task|validation hypothesis|validation step|bounded step|learning run)\b"),
)

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


def compact_todo_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_todo_action_kind(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    if TODO_ACTION_KIND_PATTERN.match(candidate):
        return candidate
    return None


def normalize_todo_task_domain(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    if TODO_TASK_DOMAIN_PATTERN.fullmatch(candidate):
        return candidate
    return None


def normalize_todo_replan_obligation_id(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if TODO_REPLAN_OBLIGATION_ID_PATTERN.fullmatch(candidate):
        return candidate
    return None


def normalize_todo_capability_binding_ref(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return None
    if TODO_CAPABILITY_BINDING_REF_PATTERN.fullmatch(candidate):
        return candidate
    return None


def normalize_todo_task_repository(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return normalize_repository_identity(candidate)
    except ValueError:
        return None


def normalize_todo_continuation_policy(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in TODO_CONTINUATION_POLICY_VALUES:
        return candidate
    return None


def normalize_removed_todo_continuation_policy(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in TODO_REMOVED_REVIEW_CONTINUATION_POLICY_VALUES:
        return candidate
    return None


def normalize_todo_claimed_by(value: Any) -> str | None:
    candidate = compact_todo_text(value).lower().replace(" ", "-")
    if candidate and TODO_AGENT_CLAIM_PATTERN.match(candidate):
        return candidate
    return None


def normalize_todo_blocks_agent(value: Any) -> str | None:
    return normalize_todo_claimed_by(value)


def normalize_todo_bound_agent(value: Any) -> str | None:
    return normalize_todo_claimed_by(value)


def normalize_todo_goal_bound(value: Any) -> bool | None:
    return normalize_todo_global_gate(value)


def normalize_todo_excluded_agents(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]
    return sorted(
        {
            agent_id
            for candidate in candidates
            for agent_id in [normalize_todo_claimed_by(candidate)]
            if agent_id
        }
    )


def require_todo_excluded_agents(
    value: Any,
    *,
    field: str = "excluded_agents",
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]
    normalized: set[str] = set()
    for candidate in candidates:
        agent_id = normalize_todo_claimed_by(candidate)
        if not agent_id:
            raise ValueError(
                f"{field} must contain public-safe agent tokens such as codex-side-bypass"
            )
        normalized.add(agent_id)
    return sorted(normalized)


def normalize_todo_global_gate(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    candidate = compact_todo_text(value).lower()
    if candidate in {"1", "true", "yes", "y", "global_gate", "global-gate"}:
        return True
    if candidate in {"0", "false", "no", "n"}:
        return False
    return None


def normalize_todo_id(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate and TODO_ID_PATTERN.match(candidate):
        return candidate
    return None


def normalize_todo_id_list(value: Any) -> list[str]:
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


def merge_todo_id_lists(*values: Any) -> list[str]:
    todo_ids: list[str] = []
    for value in values:
        for todo_id in normalize_todo_id_list(value):
            if todo_id not in todo_ids:
                todo_ids.append(todo_id)
    return todo_ids


def normalize_required_write_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "") for item in value]
    else:
        raw_values = re.split(r"[,;|]", str(value or ""))
    scopes: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        scope = compact_todo_text(raw)
        if not scope:
            continue
        if len(scope) > TODO_WRITE_SCOPE_MAX_CHARS:
            continue
        if scope.startswith(("/", "~")) or ".." in scope.split("/"):
            continue
        if re.search(r"[\s<>]", scope):
            continue
        if scope in seen:
            continue
        seen.add(scope)
        scopes.append(scope)
    return scopes


def normalize_required_capabilities(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "") for item in value]
    else:
        raw_values = re.split(r"[,;|]", str(value or ""))
    capabilities: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        capability = compact_todo_text(raw).lower().replace("-", "_").replace(" ", "_")
        if not capability:
            continue
        if not TODO_CAPABILITY_PATTERN.match(capability):
            continue
        if capability in seen:
            continue
        seen.add(capability)
        capabilities.append(capability)
    return capabilities


def normalize_target_capabilities(value: Any) -> list[str]:
    return normalize_required_capabilities(value)


def normalize_explore_result_node_refs(value: Any) -> list[str]:
    """Normalize explicit public-safe Explore node ids attached to a todo."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "") for item in value]
    else:
        raw_values = re.split(r"[,;|]", str(value or ""))
    refs: list[str] = []
    for raw in raw_values:
        ref = compact_todo_text(raw)
        if not ref or not TODO_EXPLORE_RESULT_NODE_REF_PATTERN.match(ref):
            continue
        if ref not in refs:
            refs.append(ref)
        if len(refs) >= TODO_EXPLORE_RESULT_NODE_REF_LIMIT:
            break
    return refs


def replan_successor_semantic_binding(
    *,
    action_kind: Any,
    target_key: Any,
    explore_result_node_refs: Any,
) -> dict[str, Any] | None:
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
    binding: dict[str, Any] = {"action_kind": safe_action_kind}
    if safe_target_key:
        binding["target_key"] = safe_target_key
    if safe_explore_refs:
        binding["explore_result_node_refs"] = safe_explore_refs
    return binding


def normalize_todo_decision_scope(value: Any) -> dict[str, str] | None:
    """Normalize the compact decision-scope metadata token.

    Markdown todo metadata stores the v0 shape as
    ``kind:granularity:scope_key`` so authors do not need to embed JSON in
    active-state comments. Status and quota expose the normalized object.
    """

    raw_kind: Any = None
    raw_granularity: Any = None
    raw_scope_key: Any = None
    raw_decision_id: Any = None
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


def normalize_todo_decision_outcome(value: Any) -> str | None:
    candidate = compact_todo_text(value).lower()
    return candidate if candidate in TODO_DECISION_OUTCOME_VALUES else None


def require_todo_decision_outcome(value: Any) -> str:
    outcome = normalize_todo_decision_outcome(value)
    if not outcome:
        raise ValueError(
            "decision_outcome must be one of: "
            + ", ".join(sorted(TODO_DECISION_OUTCOME_VALUES))
        )
    return outcome


def require_todo_decision_scope(value: Any) -> dict[str, str]:
    scope = normalize_todo_decision_scope(value)
    if not scope:
        raise ValueError(
            "decision_scope must use kind:granularity:scope_key, such as "
            "private_read:project:authority"
        )
    return scope


def _normalize_todo_required_decision_scopes(
    value: Any,
    *,
    strict: bool,
) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = re.split(r"[,;|]", str(value or ""))
    scopes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_values:
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


def normalize_todo_required_decision_scopes(value: Any) -> list[dict[str, str]]:
    return _normalize_todo_required_decision_scopes(value, strict=False)


def require_todo_required_decision_scopes(value: Any) -> list[dict[str, str]]:
    return _normalize_todo_required_decision_scopes(value, strict=True)


def decision_scope_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    scope = require_todo_decision_scope(value)
    return f"{scope['kind']}:{scope['granularity']}:{scope['scope_key']}"


def required_decision_scopes_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    scopes = require_todo_required_decision_scopes(value)
    if not scopes:
        return None
    return ",".join(
        f"{scope['kind']}:{scope['granularity']}:{scope['scope_key']}"
        for scope in scopes
    )


def normalize_todo_decision_scope_outcomes(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    raw_values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    if isinstance(value, str):
        raw_values = [item for item in value.split(",") if item]
    outcomes: list[dict[str, Any]] = []
    positions: dict[tuple[str, str, str], int] = {}
    for raw in raw_values:
        raw_outcome: Any = None
        raw_scope: Any = None
        raw_source_todo_id: Any = None
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
        payload = {
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


def decision_scope_outcomes_metadata_value(value: Any) -> str | None:
    outcomes = normalize_todo_decision_scope_outcomes(value)
    if not outcomes:
        return None
    return ",".join(
        "|".join(
            (
                item["outcome"],
                ":".join(
                    (
                        item["decision_scope"]["kind"],
                        item["decision_scope"]["granularity"],
                        item["decision_scope"]["scope_key"],
                    )
                ),
                item["source_todo_id"],
            )
        )
        for item in outcomes
    )


def build_todo_id(
    *,
    role: Any,
    source_section: Any,
    index: Any,
    text: Any,
) -> str:
    identity = "|".join(str(part or "") for part in (role, source_section, index, compact_todo_text(text)))
    return f"todo_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:12]}"


def normalize_explicit_todo_task_class(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in TODO_TASK_CLASS_VALUES:
        return candidate
    return None


def normalize_todo_status(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in TODO_STATUS_VALUES:
        return candidate
    return None


def normalize_todo_role(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in {"user", "agent"} else None


def todo_done_for_status(status: Any) -> bool:
    return normalize_todo_status(status) in TODO_TERMINAL_STATUS_VALUES


def todo_terminal_for_status(status: Any) -> bool:
    candidate = str(status or "").strip().lower()
    return (
        normalize_todo_status(candidate) in TODO_TERMINAL_STATUS_VALUES
        or candidate in TODO_LEGACY_TERMINAL_STATUS_VALUES
    )


def todo_status_from_marker(marker: Any) -> str:
    candidate = str(marker or "").strip().lower()
    if candidate == "x":
        return TODO_STATUS_DONE
    if candidate == "-":
        return TODO_STATUS_DEFERRED
    return TODO_STATUS_OPEN


def todo_marker_for_status(status: Any) -> str:
    normalized = normalize_todo_status(status) or TODO_STATUS_OPEN
    if normalized == TODO_STATUS_DEFERRED:
        return "-"
    if normalized == TODO_STATUS_DONE:
        return "x"
    return " "


def encode_metadata_value(value: Any) -> str:
    compact = compact_todo_text(value)
    return quote(compact, safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")


def decode_metadata_value(value: Any) -> str:
    return compact_todo_text(unquote(str(value or "")))


def parse_todo_metadata_tokens(line: str) -> list[tuple[str, str]] | None:
    match = TODO_METADATA_PATTERN.match(line)
    if not match:
        return None
    return [
        (
            token.group("key"),
            decode_metadata_value(token.group("value")),
        )
        for token in TODO_METADATA_TOKEN_PATTERN.finditer(match.group("body"))
    ]


MetadataValueNormalizer = Callable[[Any], Any]
MetadataValueEncoder = Callable[[Any], str | None]
MetadataInvalidPredicate = Callable[[Any], bool]


def _truthy_value(value: Any) -> Any:
    return value if value else None


def _nonempty_metadata_text(value: Any) -> str | None:
    text = compact_todo_text(value)
    return text or None


def _optional_output_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _metadata_bool_text(value: Any) -> str:
    return "true" if value else "false"


def _metadata_csv(value: Any) -> str:
    return ",".join(str(item) for item in value)


def _normalize_optional_decision_scope_for_write(value: Any) -> dict[str, str] | None:
    return None if value is None else require_todo_decision_scope(value)


def _normalize_optional_required_decision_scopes_for_write(
    value: Any,
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

    def normalize_for_write(self, value: Any) -> Any:
        normalizer = self.write_normalizer or self.read_normalizer
        normalized = normalizer(value)
        if (
            self.invalid_message
            and self.invalid_when(value)
            and not _metadata_value_is_present(normalized)
        ):
            raise ValueError(self.invalid_message)
        return normalized


def _metadata_value_is_present(value: Any) -> bool:
    return value is not None and value != "" and value != []


_TODO_METADATA_FIELD_SCHEMA = (
    _TodoMetadataField(
        "todo_id",
        normalize_todo_id,
        invalid_message=(
            "todo_id must use the public token shape "
            "todo_<letters-digits-underscore-hyphen>"
        ),
    ),
    _TodoMetadataField(
        "status",
        normalize_todo_status,
        invalid_message=(
            "todo status must be one of: " + ", ".join(sorted(TODO_STATUS_VALUES))
        ),
    ),
    _TodoMetadataField(
        "role",
        normalize_todo_role,
        invalid_message="todo role must be one of: agent, user",
    ),
    _TodoMetadataField(
        "task_class",
        normalize_explicit_todo_task_class,
        invalid_message=(
            "todo task_class must be one of: "
            + ", ".join(sorted(TODO_TASK_CLASS_VALUES))
        ),
    ),
    _TodoMetadataField(
        "action_kind",
        normalize_todo_action_kind,
        invalid_message=(
            "todo action_kind must be a public-safe token: lowercase letters, "
            "digits, '_' or '-'"
        ),
    ),
    _TodoMetadataField(
        "task_domain",
        normalize_todo_task_domain,
        invalid_message=(
            "todo task_domain must be a public-safe token: lowercase letters, "
            "digits, '_', '-' or '.'"
        ),
    ),
    _TodoMetadataField(
        "capability_binding_ref",
        normalize_todo_capability_binding_ref,
        invalid_message=(
            "capability_binding_ref must be a public-safe namespaced token"
        ),
    ),
    _TodoMetadataField(
        "task_repository",
        normalize_todo_task_repository,
        invalid_message=(
            "todo task_repository must be a credential-free Git remote or canonical "
            "git:<host>/<path> identity"
        ),
    ),
    _TodoMetadataField(
        "continuation_policy",
        normalize_todo_continuation_policy,
        invalid_message=(
            "todo continuation_policy must be one of: "
            + ", ".join(sorted(TODO_CONTINUATION_POLICY_VALUES))
        ),
        read_fallback_key="removed_continuation_policy",
        read_fallback_normalizer=normalize_removed_todo_continuation_policy,
    ),
    _TodoMetadataField(
        "removed_continuation_policy",
        normalize_removed_todo_continuation_policy,
        invalid_message=(
            "removed_continuation_policy must be one of: "
            + ", ".join(sorted(TODO_REMOVED_REVIEW_CONTINUATION_POLICY_VALUES))
        ),
        block_field=False,
    ),
    _TodoMetadataField(
        "required_write_scopes",
        normalize_required_write_scopes,
        encoder=_metadata_csv,
        invalid_message=(
            "required_write_scopes must contain public-safe relative scope tokens"
        ),
    ),
    _TodoMetadataField(
        "required_capabilities",
        normalize_required_capabilities,
        encoder=_metadata_csv,
        invalid_message=(
            "required_capabilities must contain public-safe capability tokens"
        ),
    ),
    _TodoMetadataField(
        "target_capabilities",
        normalize_target_capabilities,
        encoder=_metadata_csv,
        invalid_message=(
            "target_capabilities must contain public-safe capability tokens"
        ),
    ),
    _TodoMetadataField(
        "explore_result_node_refs",
        normalize_explore_result_node_refs,
        encoder=_metadata_csv,
        invalid_message=(
            "explore_result_node_refs must contain public-safe Explore node ids"
        ),
    ),
    _TodoMetadataField(
        "decision_scope",
        normalize_todo_decision_scope,
        write_normalizer=_normalize_optional_decision_scope_for_write,
        encoder=decision_scope_metadata_value,
        invalid_when=lambda value: value is not None,
    ),
    _TodoMetadataField(
        "required_decision_scopes",
        normalize_todo_required_decision_scopes,
        write_normalizer=_normalize_optional_required_decision_scopes_for_write,
        encoder=required_decision_scopes_metadata_value,
    ),
    _TodoMetadataField(
        "decision_outcome",
        normalize_todo_decision_outcome,
        invalid_message=(
            "decision_outcome must be one of: "
            + ", ".join(sorted(TODO_DECISION_OUTCOME_VALUES))
        ),
    ),
    _TodoMetadataField(
        "decision_scope_outcomes",
        normalize_todo_decision_scope_outcomes,
        encoder=decision_scope_outcomes_metadata_value,
        invalid_message=(
            "decision_scope_outcomes must contain outcome, decision_scope, and "
            "source_todo_id"
        ),
    ),
    _TodoMetadataField(
        "claimed_by",
        normalize_todo_claimed_by,
        invalid_message=(
            "claimed_by must be a public-safe agent token such as codex-main-control"
        ),
    ),
    _TodoMetadataField(
        "bound_agent",
        normalize_todo_bound_agent,
        invalid_message=(
            "bound_agent must be a public-safe agent token such as codex-main-control"
        ),
    ),
    _TodoMetadataField(
        "goal_bound",
        normalize_todo_goal_bound,
        write_normalizer=_optional_output_bool,
        encoder=_metadata_bool_text,
        invalid_when=lambda value: value is not None,
    ),
    _TodoMetadataField(
        "blocks_agent",
        normalize_todo_blocks_agent,
        invalid_message=(
            "blocks_agent must be a public-safe agent token such as codex-side-bypass"
        ),
    ),
    _TodoMetadataField(
        "excluded_agents",
        normalize_todo_excluded_agents,
        write_normalizer=require_todo_excluded_agents,
        encoder=_metadata_csv,
    ),
    _TodoMetadataField(
        "global_gate",
        normalize_todo_global_gate,
        write_normalizer=_optional_output_bool,
        encoder=_metadata_bool_text,
        invalid_when=lambda value: value is not None,
    ),
    _TodoMetadataField(
        "unblocks_todo_id",
        normalize_todo_id,
        invalid_message=(
            "unblocks_todo_id must use the public token shape "
            "todo_<letters-digits-underscore-hyphen>"
        ),
    ),
    _TodoMetadataField(
        "successor_todo_ids",
        normalize_todo_id_list,
        encoder=_metadata_csv,
        invalid_message=(
            "successor_todo_ids must contain public "
            "todo_<letters-digits-underscore-hyphen> tokens"
        ),
    ),
    _TodoMetadataField(
        "completion_continuation",
        normalize_todo_completion_continuation,
        invalid_message=(
            "completion_continuation must be one of: active_goal, successor, "
            "no_followup"
        ),
    ),
    _TodoMetadataField(
        "completion_recovery",
        normalize_todo_completion_recovery,
        invalid_message=(
            "completion_recovery must be same_turn_terminal_closeout or "
            "lifecycle_reentry_terminal_closeout"
        ),
    ),
    _TodoMetadataField(
        "replan_obligation_id",
        normalize_todo_replan_obligation_id,
        invalid_message=(
            "replan_obligation_id must use the current replan-<16 lowercase hex> "
            "identity"
        ),
    ),
    _TodoMetadataField(
        "resume_when",
        normalize_todo_resume_when,
        invalid_message=(
            "resume_when must be public-safe, e.g. "
            "todo_done:todo_ab12cd34ef56, "
            "monitor_changed:todo_monitor123, pr_merged:#532, or "
            "capacity_available:short_pool"
        ),
    ),
    _TodoMetadataField(
        "resume_monitor_generation",
        normalize_todo_generation,
        invalid_message=(
            "resume_monitor_generation must be a non-negative integer"
        ),
    ),
    _TodoMetadataField(
        "no_followup",
        normalize_todo_no_followup,
        write_normalizer=_optional_output_bool,
        encoder=_metadata_bool_text,
        invalid_when=lambda value: value is not None,
    ),
    *(
        _TodoMetadataField(
            key,
            _nonempty_metadata_text,
            write_normalizer=normalize_todo_generation
            if key == "material_change_generation"
            else _truthy_value,
        )
        for key in TODO_MONITOR_METADATA_FIELDS
    ),
    *(
        _TodoMetadataField(
            key,
            _nonempty_metadata_text,
            write_normalizer=_truthy_value,
        )
        for key in (
            "note",
            "evidence",
            "reason",
            "completed_at",
            "updated_at",
            "completion_turn_key",
            "completion_receipt_id",
            "validation_command",
            "validation_command_argv",
            "validation_label",
            "validation_timeout_seconds",
        )
    ),
    _TodoMetadataField(
        "superseded_by",
        normalize_todo_id,
        invalid_message=(
            "superseded_by must use the public token shape "
            "todo_<letters-digits-underscore-hyphen>"
        ),
    ),
)

_TODO_METADATA_FIELD_BY_TOKEN = {
    field.key: field for field in _TODO_METADATA_FIELD_SCHEMA
}


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


def parse_todo_metadata_line(line: str) -> dict[str, Any] | None:
    tokens = parse_todo_metadata_tokens(line)
    if tokens is None:
        return None
    metadata: dict[str, Any] = {}
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


def _normalize_todo_metadata_for_write(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
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
        if (
            field.key == "excluded_agents"
            and normalized.get("claimed_by")
            and normalized["claimed_by"] in normalized.get("excluded_agents", [])
        ):
            raise ValueError(
                f"claimed_by={normalized['claimed_by']!r} cannot also appear in "
                "excluded_agents"
            )
    return normalized


def normalize_todo_metadata_for_write(values: dict[str, Any]) -> dict[str, Any]:
    """Return canonical typed Todo metadata without Markdown encoding."""

    return _normalize_todo_metadata_for_write(values)


def _format_todo_metadata_values(values: dict[str, Any]) -> str | None:
    normalized = _normalize_todo_metadata_for_write(values)
    fields = [
        f"{field.key}={encode_metadata_value(field.encoder(normalized[field.key]))}"
        for field in _TODO_METADATA_FIELD_SCHEMA
        if field.key in normalized
    ]
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
    required_write_scopes: Any = None,
    required_capabilities: Any = None,
    target_capabilities: Any = None,
    explore_result_node_refs: Any = None,
    decision_scope: Any = None,
    required_decision_scopes: Any = None,
    decision_outcome: str | None = None,
    decision_scope_outcomes: Any = None,
    claimed_by: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool | None = None,
    blocks_agent: str | None = None,
    excluded_agents: Any = None,
    global_gate: bool | None = None,
    unblocks_todo_id: str | None = None,
    successor_todo_ids: Any = None,
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


def todo_block_metadata(block: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in TODO_METADATA_FIELDS:
        value = block.get(key)
        if value is None:
            continue
        normalized: Any
        if key == "required_write_scopes":
            normalized = normalize_required_write_scopes(value)
        elif key == "task_domain":
            normalized = normalize_todo_task_domain(value)
        elif key == "task_repository":
            normalized = normalize_todo_task_repository(value)
        elif key == "required_capabilities":
            normalized = normalize_required_capabilities(value)
        elif key == "target_capabilities":
            normalized = normalize_target_capabilities(value)
        elif key == "excluded_agents":
            normalized = normalize_todo_excluded_agents(value)
        elif key == "explore_result_node_refs":
            normalized = normalize_explore_result_node_refs(value)
        elif key == "continuation_policy":
            normalized = normalize_todo_continuation_policy(value)
        elif key == "decision_scope":
            normalized = normalize_todo_decision_scope(value)
        elif key == "required_decision_scopes":
            normalized = normalize_todo_required_decision_scopes(value)
        elif key == "decision_outcome":
            normalized = normalize_todo_decision_outcome(value)
        elif key == "decision_scope_outcomes":
            normalized = normalize_todo_decision_scope_outcomes(value)
        elif key == "no_followup":
            normalized = normalize_todo_no_followup(value)
        elif key == "completion_continuation":
            normalized = normalize_todo_completion_continuation(value)
        elif key == "completion_recovery":
            normalized = normalize_todo_completion_recovery(value)
        elif key in {"global_gate", "goal_bound"}:
            normalized = normalize_todo_global_gate(value)
        else:
            normalized = str(value).strip()
        if normalized is not None and normalized != [] and normalized != "":
            metadata[key] = normalized
    return metadata


def metadata_line_for_todo_block(
    block: dict[str, Any],
    updates: dict[str, Any],
) -> str | None:
    metadata = todo_block_metadata(block)
    for key, value in updates.items():
        if key not in TODO_METADATA_FIELDS:
            continue
        if value is None:
            metadata.pop(key, None)
        elif key == "required_write_scopes":
            normalized = normalize_required_write_scopes(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key == "task_domain":
            metadata[key] = _TODO_METADATA_FIELD_BY_TOKEN[
                key
            ].normalize_for_write(value)
        elif key == "task_repository":
            normalized_repository = normalize_todo_task_repository(value)
            if normalized_repository:
                metadata[key] = normalized_repository
            else:
                metadata.pop(key, None)
        elif key == "required_capabilities":
            normalized = normalize_required_capabilities(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key == "target_capabilities":
            normalized = normalize_target_capabilities(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key == "excluded_agents":
            normalized = normalize_todo_excluded_agents(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key == "explore_result_node_refs":
            normalized = normalize_explore_result_node_refs(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key == "continuation_policy":
            normalized_continuation_policy = normalize_todo_continuation_policy(value)
            if normalized_continuation_policy:
                metadata[key] = normalized_continuation_policy
            elif value:
                raise ValueError(
                    "todo continuation_policy must be one of: "
                    + ", ".join(sorted(TODO_CONTINUATION_POLICY_VALUES))
                )
            else:
                metadata.pop(key, None)
        elif key == "decision_scope":
            metadata[key] = require_todo_decision_scope(value)
        elif key == "required_decision_scopes":
            normalized_decision_scopes = require_todo_required_decision_scopes(value)
            if normalized_decision_scopes:
                metadata[key] = normalized_decision_scopes
            else:
                metadata.pop(key, None)
        elif key == "decision_outcome":
            metadata[key] = require_todo_decision_outcome(value)
        elif key == "decision_scope_outcomes":
            normalized_scope_outcomes = normalize_todo_decision_scope_outcomes(value)
            if normalized_scope_outcomes:
                metadata[key] = normalized_scope_outcomes
            else:
                metadata.pop(key, None)
        elif key == "no_followup":
            normalized_no_followup = normalize_todo_no_followup(value)
            if normalized_no_followup is not None:
                metadata[key] = normalized_no_followup
            else:
                metadata.pop(key, None)
        elif key == "successor_todo_ids":
            normalized = normalize_todo_id_list(value)
            if normalized:
                metadata[key] = normalized
            else:
                metadata.pop(key, None)
        elif key in {"completion_continuation", "completion_recovery"}:
            normalized_completion = require_todo_completion_metadata(key, value)
            if normalized_completion:
                metadata[key] = normalized_completion
            else:
                metadata.pop(key, None)
        elif key in {"global_gate", "goal_bound"}:
            normalized_global_gate = normalize_todo_global_gate(value)
            if normalized_global_gate is not None:
                metadata[key] = normalized_global_gate
            else:
                metadata.pop(key, None)
        elif str(value).strip():
            metadata[key] = str(value).strip()
    if "todo_id" not in metadata and block.get("todo_id"):
        metadata["todo_id"] = str(block["todo_id"])
    if "status" not in metadata:
        metadata["status"] = TODO_STATUS_DONE if block.get("done") else TODO_STATUS_OPEN
    return format_todo_metadata_line(**metadata)


def todo_task_class_for_text(text: str) -> str:
    compact = compact_todo_text(text)
    for pattern in TODO_HARD_MONITOR_PATTERNS:
        if pattern.search(compact):
            return TODO_TASK_CLASS_MONITOR
    for pattern in TODO_ADVANCEMENT_OVERRIDE_PATTERNS:
        if pattern.search(compact):
            return TODO_TASK_CLASS_ADVANCEMENT
    for pattern in TODO_BLOCKED_MONITOR_PATTERNS:
        if pattern.search(compact):
            return TODO_TASK_CLASS_MONITOR
    for pattern in TODO_MONITOR_PATTERNS:
        if pattern.search(compact):
            return TODO_TASK_CLASS_MONITOR
    for pattern in TODO_ADVANCEMENT_PATTERNS:
        if pattern.search(compact):
            return TODO_TASK_CLASS_ADVANCEMENT
    return TODO_TASK_CLASS_ADVANCEMENT


def normalize_todo_task_class(value: Any, *, text: str, action_kind: Any = None) -> str:
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
