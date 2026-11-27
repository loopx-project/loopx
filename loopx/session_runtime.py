"""Read-only session-runtime projection for the LoopX first screen.

Raw-material classification is typed and word-level. Input keys are split into
words on ``_``, ``-``, and camelCase, then matched against exact keys, whole
words, or exact word sequences, never arbitrary substrings. The earlier
substring denylist flagged ``token_count`` and ``catalog_id`` while missing
``messages`` and ``api_key``.
Every key lands in one of three states: known compact keys are allowed, known
raw-material keys are flagged with a :class:`RawMaterialCategory`, and
unrecognized keys are reported as ``unclassified_key_names`` so producers can
see contract drift without being blocked by it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from itertools import chain
import re
from typing import NamedTuple, TypedDict

SESSION_RUNTIME_READONLY_PROJECTION_SCHEMA_VERSION = (
    "session_runtime_readonly_projection_v0"
)
UNCLASSIFIED_KEY_LIMIT = 24


class KeyState(str, Enum):
    COMPACT = "compact"
    RAW_MATERIAL = "raw_material"
    UNCLASSIFIED = "unclassified"


class RawMaterialCategory(str, Enum):
    CREDENTIAL = "credential"
    TRANSCRIPT = "transcript"
    LOG = "log"
    LOCAL_PATH = "local_path"
    RAW_OUTPUT = "raw_output"


class KeyClassification(NamedTuple):
    state: KeyState
    category: RawMaterialCategory | None = None


class SourceRefs(TypedDict):
    sessions: list[dict[str, str]]
    events: list[dict[str, str]]
    outcomes: list[dict[str, str]]
    gates: list[dict[str, str]]
    artifacts: list[dict[str, str]]
    decision_results: list[dict[str, str]]


class SessionRuntimeSource(TypedDict):
    host_kind: str
    source_refs: SourceRefs
    latest_fact_at: str | None


class SessionRuntimeBoundary(TypedDict):
    raw_transcript_copied: bool
    raw_logs_copied: bool
    credentials_copied: bool
    runtime_writeback_allowed: bool
    runtime_mutation_allowed: bool
    raw_material_detected: bool
    raw_material_key_names: list[str]
    raw_material_categories: list[str]
    unclassified_key_names: list[str]


class SessionRuntimeFirstScreen(TypedDict):
    waiting_on: str
    user_action_required: bool
    agent_can_continue: bool
    first_user_todo: str | None
    first_agent_todo: str | None
    latest_validation: str | None
    latest_blocker: str | None
    gate_state: str
    recommended_action: str | None


class SessionRuntimeWorkLaneContract(TypedDict):
    lane: str
    must_attempt_work: bool
    user_gate_blocks_delivery: bool
    monitor_only: bool


class SessionRuntimeAttentionItem(TypedDict):
    kind: str
    priority: str
    title: str | None
    waiting_on: str
    evidence_refs: SourceRefs


class SessionRuntimeReconcileRule(TypedDict):
    raw_fact_source: str
    compact_projection_source: str
    conflict_policy: str


class SessionRuntimeReadonlyProjection(TypedDict):
    schema_version: str
    goal_id: str
    mode: str
    source: SessionRuntimeSource
    boundary: SessionRuntimeBoundary
    first_screen: SessionRuntimeFirstScreen
    work_lane_contract: SessionRuntimeWorkLaneContract
    attention_item: SessionRuntimeAttentionItem
    reconcile_rule: SessionRuntimeReconcileRule


SOURCE_ID_KEYS = (
    "session_id", "event_id", "outcome_id", "gate_id", "approval_id",
    "artifact_id", "tool_call_id", "run_id", "ref_id",
)
TIMESTAMP_KEYS = ("created_at", "event_at", "updated_at", "timestamp")

# Public-safe pointers and aggregate counters whose leading word otherwise
# carries raw-material meaning. Keep these exceptions explicit and reviewable.
EXPLICIT_COMPACT_COLLISION_KEYS = frozenset(
    {"conversation_id", "log_count", "message_id", "prompt_token_count", "prompt_tokens", "trace_id"}
)

# Exact keys the projection itself reads, plus its input booleans.
COMPACT_KEYS = frozenset(
    {
        *SOURCE_ID_KEYS,
        *TIMESTAMP_KEYS,
        *EXPLICIT_COMPACT_COLLISION_KEYS,
        "kind", "type", "status", "state", "actor", "required_actor",
        "decision_actor", "channel", "requires_human_decision", "action_required",
        "advisory", "blocking", "question", "requested_decision", "title",
        "summary", "next_action", "recommended_action", "agent_next_action",
        "handoff", "validation_summary", "validated", "result", "blocker",
        "blocker_summary",
    }
)

# A generic pointer/count suffix is compact only when no exact or word-level
# raw-material evidence matched first.
COMPACT_SUFFIX_WORDS = frozenset({"id", "ids", "ref", "refs", "count", "at"})
# Usage metrics: ``tokens_used``, ``max_tokens``, ``input_tokens``.
COMPACT_METRIC_WORDS = frozenset({"tokens"})

# Exact keys that are raw material even though their words are individually
# ambiguous (``key``, ``token``, ``content``, ``output``).
RAW_MATERIAL_KEYS: Mapping[str, RawMaterialCategory] = {
    **dict.fromkeys(
        (
            "token", "access_token", "auth_token", "api_token", "bearer_token",
            "refresh_token", "id_token", "session_token", "api_key", "apikey",
            "private_key", "secret_key", "access_key", "authorization", "cookie", "cookies",
        ),
        RawMaterialCategory.CREDENTIAL,
    ),
    **dict.fromkeys(
        ("content", "body", "request_body", "response_body", "message"),
        RawMaterialCategory.TRANSCRIPT,
    ),
    **dict.fromkeys(
        ("output", "output_text", "tool_output", "tool_result"),
        RawMaterialCategory.RAW_OUTPUT,
    ),
}

# Whole words that mark raw material in any position. Category precedence is
# the tuple order, so ``raw_transcript`` is a transcript and ``raw_log`` a log.
RAW_MATERIAL_WORDS: tuple[tuple[RawMaterialCategory, frozenset[str]], ...] = (
    (RawMaterialCategory.CREDENTIAL, frozenset({"credential", "credentials", "secret", "secrets", "password", "passwd", "passphrase"})),
    (RawMaterialCategory.TRANSCRIPT, frozenset({"transcript", "transcripts", "messages", "prompt", "prompts", "conversation"})),
    (RawMaterialCategory.LOG, frozenset({"log", "logs", "trace", "traces", "stacktrace", "traceback"})),
    (RawMaterialCategory.LOCAL_PATH, frozenset({"path", "paths", "cwd", "workdir", "filename", "filepath"})),
    (RawMaterialCategory.RAW_OUTPUT, frozenset({"raw", "stdout", "stderr", "diff", "patch", "dump"})),
)

# Multi-word exact raw keys also remain raw when embedded in a larger key. This
# catches forms such as ``api_key_id`` without treating the ambiguous word
# ``key`` (or a harmless key such as ``monkey_id``) as raw material.
RAW_MATERIAL_KEY_PHRASES: Mapping[RawMaterialCategory, tuple[tuple[str, ...], ...]] = {
    category: tuple(
        tuple(key.split("_"))
        for key, key_category in RAW_MATERIAL_KEYS.items()
        if key_category is category and "_" in key
    )
    for category, _raw_words in RAW_MATERIAL_WORDS
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_SEPARATOR = re.compile(r"[^a-z0-9]+")

OPEN_GATE_STATUSES = {
    "blocked", "needs_decision", "open", "pending",
    "requested", "requires_decision", "waiting",
}
HUMAN_GATE_ACTORS = {"controller", "human", "operator", "owner", "user"}
BLOCKED_STATUSES = {"blocked", "error", "failed", "timed_out"}
VALIDATED_STATUSES = {"ok", "passed", "success", "validated"}


def _as_mappings(values: Sequence[Mapping[str, object]] | None) -> tuple[Mapping[str, object], ...]:
    if not values:
        return ()
    # Exact builtin dicts cannot compute or cache missing keys on lookup; other
    # Mappings are snapshotted from their enumerated keys to stay read-only.
    return tuple(
        item if type(item) is dict else dict(item)
        for item in values
        if isinstance(item, Mapping)
    )


def _text(value: object, *, limit: int = 180) -> str | None:
    if value is None:
        return None
    raw = value if isinstance(value, str) else str(value)
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _first_text(item: Mapping[str, object], keys: Sequence[str]) -> str | None:
    for key in keys:
        text = _text(item.get(key))
        if text:
            return text
    return None


def _status(item: Mapping[str, object]) -> str:
    return str(item.get("status") or item.get("state") or "").strip().lower()


def _kind(item: Mapping[str, object]) -> str:
    return str(item.get("kind") or item.get("type") or "").strip().lower()


def _actor(item: Mapping[str, object]) -> str:
    return str(
        item.get("actor")
        or item.get("required_actor")
        or item.get("decision_actor")
        or item.get("channel")
        or ""
    ).strip().lower()


def _event_timestamp(item: Mapping[str, object]) -> object:
    return (
        item.get("created_at")
        or item.get("event_at")
        or item.get("updated_at")
        or item.get("timestamp")
    )


def _is_blocking_gate(item: Mapping[str, object]) -> bool:
    if _status(item) not in OPEN_GATE_STATUSES:
        return False
    human_targeted = (
        _actor(item) in HUMAN_GATE_ACTORS
        or bool(item.get("requires_human_decision"))
        or bool(item.get("action_required"))
    )
    return human_targeted and item.get("advisory") is not True and item.get("blocking", True) is not False


def _is_blocker(item: Mapping[str, object]) -> bool:
    return _kind(item) == "blocker" or _status(item) in BLOCKED_STATUSES


def _is_validation(item: Mapping[str, object]) -> bool:
    return _kind(item) in {"validation", "outcome"} and _status(item) in VALIDATED_STATUSES


def _latest(items: Iterable[Mapping[str, object]]) -> Mapping[str, object] | None:
    return max(items, key=lambda item: str(_event_timestamp(item) or ""), default=None)


def _source_ref(item: Mapping[str, object], *, default_kind: str) -> dict[str, str]:
    ref = {"kind": _text(item.get("kind") or item.get("type") or default_kind, limit=60) or default_kind}
    for key in SOURCE_ID_KEYS:
        value = _text(item.get(key), limit=120)
        if value:
            ref[key] = value
    return ref


def _source_refs(
    *,
    sessions: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    gates: Sequence[Mapping[str, object]],
    artifacts: Sequence[Mapping[str, object]],
    decision_results: Sequence[Mapping[str, object]],
) -> SourceRefs:
    return {
        "sessions": [_source_ref(item, default_kind="session") for item in sessions],
        "events": [_source_ref(item, default_kind="event") for item in events],
        "outcomes": [_source_ref(item, default_kind="outcome") for item in outcomes],
        "gates": [_source_ref(item, default_kind="gate") for item in gates],
        "artifacts": [_source_ref(item, default_kind="artifact") for item in artifacts],
        "decision_results": [_source_ref(item, default_kind="decision_result") for item in decision_results],
    }


def _key_words(key: str) -> list[str]:
    snake = _CAMEL_BOUNDARY.sub("_", str(key)).lower()
    return [word for word in _WORD_SEPARATOR.split(snake) if word]


def _contains_word_sequence(words: Sequence[str], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    return any(tuple(words[index : index + width]) == phrase for index in range(len(words) - width + 1))


def classify_session_runtime_key(key: str) -> KeyClassification:
    """Classify one input key as compact, raw material, or unclassified.

    Matching is exact-key, whole-word, or exact word-sequence only; substrings
    never match. Raw evidence outranks generic pointer and metric shortcuts.
    """
    if isinstance(key, str):
        if key in COMPACT_KEYS:
            return KeyClassification(KeyState.COMPACT)
        exact_category = RAW_MATERIAL_KEYS.get(key)
        if exact_category is not None:
            return KeyClassification(KeyState.RAW_MATERIAL, exact_category)
    words = _key_words(key)
    if not words:
        return KeyClassification(KeyState.UNCLASSIFIED)
    normalized = "_".join(words)
    if normalized in COMPACT_KEYS:
        return KeyClassification(KeyState.COMPACT)
    category = RAW_MATERIAL_KEYS.get(normalized)
    if category is not None:
        return KeyClassification(KeyState.RAW_MATERIAL, category)
    for category, raw_words in RAW_MATERIAL_WORDS:
        if raw_words.intersection(words) or any(
            _contains_word_sequence(words, phrase) for phrase in RAW_MATERIAL_KEY_PHRASES[category]
        ):
            return KeyClassification(KeyState.RAW_MATERIAL, category)
    if words[-1] in COMPACT_SUFFIX_WORDS or COMPACT_METRIC_WORDS.intersection(words):
        return KeyClassification(KeyState.COMPACT)
    return KeyClassification(KeyState.UNCLASSIFIED)


def _classify_keys(*groups: Sequence[Mapping[str, object]]) -> tuple[list[str], list[str], list[str]]:
    """Return sorted raw-material key names, their categories, and unclassified names."""
    raw_keys: set[str] = set()
    categories: set[str] = set()
    unclassified: set[str] = set()
    seen: set[str] = set()
    for group in groups:
        for item in group:
            for key in item:
                key_name = key if isinstance(key, str) else str(key)
                if key_name in seen:
                    continue
                seen.add(key_name)
                classification = classify_session_runtime_key(key_name)
                if classification.state is KeyState.RAW_MATERIAL:
                    raw_keys.add(key_name)
                    if classification.category is not None:
                        categories.add(classification.category.value)
                elif classification.state is KeyState.UNCLASSIFIED:
                    unclassified.add(key_name)
    return sorted(raw_keys), sorted(categories), sorted(unclassified)[:UNCLASSIFIED_KEY_LIMIT]


def _first_user_todo(gate: Mapping[str, object] | None) -> str | None:
    if gate is None:
        return None
    return _first_text(gate, ("question", "requested_decision", "title", "summary", "next_action"))


def _first_agent_todo(
    *,
    decision_results: Sequence[Mapping[str, object]],
    sessions: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> str | None:
    todo_keys = ("recommended_action", "agent_next_action", "next_action", "handoff", "summary")
    for group in (decision_results, sessions, events, outcomes):
        for item in group:
            text = _first_text(item, todo_keys)
            if text:
                return text
    return None


def _latest_validation(
    outcomes: Sequence[Mapping[str, object]], events: Sequence[Mapping[str, object]]
) -> str | None:
    latest = _latest(
        item
        for item in chain(outcomes, events)
        if _is_validation(item) or _first_text(item, ("validation_summary", "validated", "result"))
    )
    if latest is None:
        return None
    return _first_text(latest, ("validation_summary", "validated", "result", "summary"))


def _latest_blocker(
    gates: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
) -> str | None:
    latest = _latest(
        item
        for item in chain(gates, outcomes, events)
        if _is_blocker(item) or _first_text(item, ("blocker", "blocker_summary"))
    )
    if latest is None:
        return None
    return _first_text(latest, ("blocker", "blocker_summary", "summary", "title"))


def _waiting_and_lane(
    *,
    blocking_gate: Mapping[str, object] | None,
    blocker: str | None,
    first_agent_todo: str | None,
) -> tuple[str, str]:
    if blocking_gate is not None:
        actor = _actor(blocking_gate)
        return (actor if actor in HUMAN_GATE_ACTORS else "operator"), "user_gate"
    if blocker and not first_agent_todo:
        return "runtime", "blocker"
    if first_agent_todo:
        return "agent", "advancement_task"
    return "none", "monitor"


def build_session_runtime_readonly_projection(
    *,
    goal_id: str,
    sessions: Sequence[Mapping[str, object]] | None = None,
    events: Sequence[Mapping[str, object]] | None = None,
    outcomes: Sequence[Mapping[str, object]] | None = None,
    gates: Sequence[Mapping[str, object]] | None = None,
    artifacts: Sequence[Mapping[str, object]] | None = None,
    decision_results: Sequence[Mapping[str, object]] | None = None,
) -> SessionRuntimeReadonlyProjection:
    """Project compact session-runtime facts into a LoopX first screen.

    This adapter is intentionally read-only. It consumes only compact summaries
    and source pointers, never raw transcripts, logs, credentials, or local
    paths. Known raw-material keys are recorded as a boundary violation without
    copying their values; unrecognized keys are reported but do not block.
    """
    session_items = _as_mappings(sessions)
    event_items = _as_mappings(events)
    outcome_items = _as_mappings(outcomes)
    gate_items = _as_mappings(gates)
    artifact_items = _as_mappings(artifacts)
    decision_items = _as_mappings(decision_results)

    blocking_gate = next((gate for gate in gate_items if _is_blocking_gate(gate)), None)
    user_todo = _first_user_todo(blocking_gate)
    agent_todo = _first_agent_todo(
        decision_results=decision_items, sessions=session_items, events=event_items, outcomes=outcome_items
    )
    validation = _latest_validation(outcome_items, event_items)
    blocker = _latest_blocker(gate_items, outcome_items, event_items)
    waiting, lane = _waiting_and_lane(
        blocking_gate=blocking_gate, blocker=blocker, first_agent_todo=agent_todo
    )
    raw_keys, raw_categories, unclassified_keys = _classify_keys(
        session_items, event_items, outcome_items, gate_items, artifact_items, decision_items
    )
    latest_event = _latest(chain(session_items, event_items, outcome_items))
    source_refs = _source_refs(
        sessions=session_items, events=event_items, outcomes=outcome_items,
        gates=gate_items, artifacts=artifact_items, decision_results=decision_items,
    )

    can_continue = bool(agent_todo and blocking_gate is None and not raw_keys)
    recommended_action = (
        "provide compact summaries without raw material before projection"
        if raw_keys
        else user_todo
        if blocking_gate is not None
        else agent_todo
        if agent_todo
        else "monitor for a material transition"
    )
    latest_fact_at = _text(_event_timestamp(latest_event), limit=80) if latest_event else None

    return {
        "schema_version": SESSION_RUNTIME_READONLY_PROJECTION_SCHEMA_VERSION,
        "goal_id": str(goal_id),
        "mode": "read_only",
        "source": {"host_kind": "session_runtime", "source_refs": source_refs, "latest_fact_at": latest_fact_at},
        "boundary": {
            "raw_transcript_copied": False, "raw_logs_copied": False, "credentials_copied": False,
            "runtime_writeback_allowed": False, "runtime_mutation_allowed": False,
            "raw_material_detected": bool(raw_keys),
            "raw_material_key_names": raw_keys,
            "raw_material_categories": raw_categories,
            "unclassified_key_names": unclassified_keys,
        },
        "first_screen": {
            "waiting_on": waiting,
            "user_action_required": blocking_gate is not None,
            "agent_can_continue": can_continue,
            "first_user_todo": user_todo,
            "first_agent_todo": agent_todo if blocking_gate is None else None,
            "latest_validation": validation,
            "latest_blocker": blocker,
            "gate_state": _status(blocking_gate) if blocking_gate else "none",
            "recommended_action": recommended_action,
        },
        "work_lane_contract": {
            "lane": lane, "must_attempt_work": can_continue,
            "user_gate_blocks_delivery": blocking_gate is not None, "monitor_only": lane == "monitor",
        },
        "attention_item": {
            "kind": lane, "priority": "P0" if lane in {"user_gate", "blocker"} else "P1",
            "title": recommended_action, "waiting_on": waiting, "evidence_refs": source_refs,
        },
        "reconcile_rule": {
            "raw_fact_source": "host_session_log",
            "compact_projection_source": "loopx_run_projection",
            "conflict_policy": (
                "recompute projection from compact source refs; do not copy raw "
                "transcripts, raw logs, credentials, billing records, or sandbox "
                "internals into LoopX state"
            ),
        },
    }
