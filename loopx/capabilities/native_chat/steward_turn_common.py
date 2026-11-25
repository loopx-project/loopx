"""Shared typed protocols, narrowing helpers, and evidence utilities for StewardTurnHost."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from ...chat_runtime import ChatRuntimeAdapter

MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS = 300
_WORKER_EXITED_ERROR = (
    "The LoopX Chat execution worker exited before this turn finished. "
    "Your conversation is saved; execution may have occurred. This turn was not retried automatically."
)
_POST_CONTEXT_WATCH_ERRORS = frozenset({
    "steward monitor external signal source temporarily unavailable",
    "the portfolio scope changed after observation",
    "disk I/O error",
    _WORKER_EXITED_ERROR,
})
_FILE_LOCK_TIMEOUT_ERROR_RE = re.compile(
    r"^file lock acquisition timed out \((?:recorded holder pid [1-9]\d*|lock busy, current holder unknown)\)$"
)
_USER_ACTION_DEDUPE_KEY = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,119}$")
_REPORT_VALIDATOR_PREFIX = (
    "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
    "validate_private_generation(Path("
)


class ClosedFlagProtocol(Protocol):
    """Minimal shutdown flag surface exposed by the Chat controller."""

    def is_set(self) -> bool: ...


class AcceptedTurnProtocol(Protocol):
    """Minimal accepted-turn result returned by store.accept_managed_turn."""

    @property
    def turn(self) -> dict[str, object]: ...

    @property
    def created(self) -> bool: ...


class MonitorRestartCarrier(TypedDict, total=False):
    """Typed carrier returned by monitor_restart_carrier while preserving dict keys."""

    turn: dict[str, object]
    request: dict[str, object]
    client_id: str


class StewardStoreProtocol(Protocol):
    """Chat turn/session persistence surface consumed by StewardTurnHost."""

    def load_session(self, session_id: str) -> dict[str, object] | None: ...

    def update_session(
        self,
        session_id: str,
        *,
        expected_statuses: set[str] | None = None,
        expected_upstream_thread_id: str | None = None,
        metadata_review: bool = False,
        quota_recovery: bool = False,
        answer_review_admission: bool = False,
        **changes: object,
    ) -> object: ...

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None: ...

    def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, object] | None: ...

    def events_after(self, session_id: str, turn_id: str, event_id: str | None) -> list[dict[str, object]]: ...

    def messages(self, session_id: str) -> list[dict[str, object]]: ...

    def queued_turns(self, session_id: str) -> list[dict[str, object]]: ...

    def accept_managed_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        display_message: str | None = None,
        origin: str = "web",
        loopx_execution: bool = False,
        loopx_request: dict[str, object] | None = None,
    ) -> AcceptedTurnProtocol: ...

    def append_event(
        self,
        session_id: str,
        turn_id: str,
        *,
        kind: str,
        payload: dict[str, object],
    ) -> object: ...

    def turn_execution_lock_path(self, session_id: str, turn_id: str) -> Path: ...

    def _event_path(self, session_id: str, turn_id: str) -> Path: ...


class StewardBindingsProtocol(Protocol):
    """Conversation-bindings registry surface consumed by StewardTurnHost."""

    @property
    def path(self) -> Path: ...

    def read(self) -> dict[str, object]: ...

    def resolve(
        self,
        *,
        binding_id: str,
        source_ref: str,
        sender_ref: str,
        private_human_message: bool,
        session_context: dict[str, object] | None = None,
        include_observation: bool = False,
        origin: str | None = None,
        session: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def _resolve_observed(
        self,
        current: dict[str, object],
        row: dict[str, object],
        observation: dict[str, object],
        source: dict[str, object],
    ) -> dict[str, object]: ...

    def _session_observation(self, session: dict[str, object]) -> object: ...

    def steward_scope(self, session: dict[str, object]) -> list[str] | None: ...

    def _core(self, operation: str, payload: dict[str, object]) -> dict[str, object]: ...


class StewardProjectContextsProtocol(Protocol):
    """Project-context container carrying optional conversation bindings."""

    @property
    def conversation_bindings(self) -> StewardBindingsProtocol | None: ...


class StewardManagedProtocol(Protocol):
    """Managed-mode helper surface attached to StewardTurnHost."""

    def _workspace(self, session: Mapping[str, object]) -> Path | str: ...

    def _goal(self, goal_id: str) -> dict[str, object] | None: ...


class StewardControllerProtocol(Protocol):
    """Outer Chat controller surface consumed by StewardTurnHost."""

    @property
    def registry_path(self) -> Path | None: ...

    @property
    def coordination_runtime_root(self) -> Path: ...

    @property
    def lock(self) -> AbstractContextManager[object]: ...

    @property
    def closed(self) -> ClosedFlagProtocol: ...

    @property
    def store(self) -> StewardStoreProtocol: ...

    @property
    def project_contexts(self) -> StewardProjectContextsProtocol: ...

    def _ensure_adapter_locked(
        self,
        session: dict[str, object],
        *,
        work_dir: Path,
        objective: str,
        accepted_turn_id: str | None = None,
    ) -> object: ...

    def _start_accepted_turn_worker(
        self,
        *,
        session_id: str,
        turn_id: str,
        message: str,
        attachments: list[dict[str, object]],
        adapter: ChatRuntimeAdapter,
        loopx_execution: bool,
    ) -> object: ...

    def _fail_queue_preparation(self, session_id: str, turn_id: str, error: Exception) -> None: ...


class StewardTurnHostProtocol(Protocol):
    """Internal host protocol decoupling recovery/execution helpers from StewardTurnHost."""

    controller: StewardControllerProtocol
    store: StewardStoreProtocol
    bindings: StewardBindingsProtocol | None
    managed: StewardManagedProtocol

    def _observe(self, actor: Mapping[str, object]) -> dict[str, object] | None: ...

    def _admit(
        self,
        request: dict[str, object],
        *,
        turn: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
        carrier_session: dict[str, object] | None = None,
        prepare_context: bool = False,
        settlement_recovery: dict[str, object] | None = None,
        frozen_contract_recovery: dict[str, object] | None = None,
        launch_recovery: dict[str, object] | None = None,
        selection_recovery: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def report_failure_observation(self, original: Mapping[str, object]) -> dict[str, object] | None: ...

    def report_selection_recovery(
        self,
        original: dict[str, object],
        *,
        probe_turn: dict[str, object] | None = None,
    ) -> dict[str, object] | None: ...

    def _cleared_replan_guard(self, actor: Mapping[str, object]) -> dict[str, object] | None: ...

    def _report_validator(
        self,
        request: Mapping[str, object],
    ) -> Callable[[Mapping[str, object], Mapping[str, object]], dict[str, object]]: ...

    def _report_fence(self, request: Mapping[str, object]) -> AbstractContextManager[object]: ...

    def _task_lease_proof(
        self,
        request: Mapping[str, object],
        lease: Mapping[str, object] | None,
    ) -> dict[str, object]: ...

    def _task_execution_lease(
        self,
        request: Mapping[str, object],
        turn_id: str,
        *,
        recovery: bool = False,
        admission_retry: bool = False,
        claim_args: list[str] | None = None,
        retain_for_retry: Callable[[], bool] | None = None,
    ) -> AbstractContextManager[dict[str, object]]: ...

    def _frozen_report_contract_recovery(
        self,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        original: Mapping[str, object],
        *,
        key: str,
        generation: Mapping[str, object],
        root: Path,
    ) -> tuple[dict[str, object], dict[str, object]] | None: ...

    def _stopped_report_candidate_recovery(
        self,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        original: Mapping[str, object],
        *,
        key: str,
        folder: Path,
        root: Path,
    ) -> tuple[dict[str, object], dict[str, object]] | None: ...

    def _settle_stopped_report_candidate(
        self,
        *,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        todo: Mapping[str, object],
        original: Mapping[str, object],
        request: dict[str, object],
        observed: dict[str, object] | None,
        reports: object,
        owner_key: str,
        folder: Path,
        original_key: str,
        stopped_proof: dict[str, object],
        raw_candidate: dict[str, object],
    ) -> dict[str, object]: ...

    def _steward_followup_context(
        self,
        actor: Mapping[str, object],
        session_id: str,
        request: Mapping[str, object],
        authority: Mapping[str, object],
    ) -> dict[str, object]: ...

    def _governance_replan_context(
        self,
        actor: Mapping[str, object],
        authority: Mapping[str, object],
    ) -> dict[str, object]: ...

    def _apply_steward_user_actions(
        self,
        actor: Mapping[str, object],
        actions: Sequence[Mapping[str, str]],
        turn_instance_id: str,
    ) -> list[dict[str, object]]: ...

    @staticmethod
    def _build_replay_runner(
        normalized_candidate: Mapping[str, object],
    ) -> Callable[[Mapping[str, object], object], dict[str, object]]: ...

    @staticmethod
    def _normalize_steward_actions(
        candidate: Mapping[str, object],
        context: Mapping[str, object],
    ) -> dict[str, object]: ...


def as_mapping(value: object) -> Mapping[str, object]:
    """Return value as a typed string-keyed Mapping without copying dicts."""
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def as_dict(value: object) -> dict[str, object]:
    """Return value as a mutable string-keyed dict, preserving dict identity."""
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    if isinstance(value, Mapping):
        return {str(k): v for k, v in cast(Mapping[object, object], value).items()}
    return {}


def as_optional_dict(value: object) -> dict[str, object] | None:
    """Return value as a typed dict when Mapping, otherwise None."""
    if isinstance(value, Mapping):
        return as_dict(value)
    return None


def as_dict_list(value: object) -> list[dict[str, object]]:
    """Extract Mapping items from a sequence/list into typed dicts."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [as_dict(item) for item in value if isinstance(item, Mapping)]


def as_str(value: object, default: str = "") -> str:
    """Return value when it is a str, otherwise default."""
    return value if isinstance(value, str) else default


def as_optional_str(value: object) -> str | None:
    """Return value when it is a str, otherwise None."""
    return value if isinstance(value, str) else None


def as_str_list(value: object) -> list[str]:
    """Extract string items from a sequence/list."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, str)]


def as_int(value: object, default: int = 0) -> int:
    """Return value when it is a non-bool int, otherwise default."""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def iso_timestamp_seconds(value: object) -> float | None:
    """Parse an ISO-8601 timestamp into UTC epoch seconds, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def same_report_validator_command(frozen_cmd: object, request_cmd: object) -> bool:
    """Allow report validation commands whose only drift is the host Python path."""
    return (
        isinstance(frozen_cmd, list)
        and isinstance(request_cmd, list)
        and len(frozen_cmd) == 3
        and len(request_cmd) == 3
        and all(isinstance(part, str) and bool(part) for part in [*frozen_cmd, *request_cmd])
        and frozen_cmd[1] == "-c"
        and request_cmd[1] == "-c"
        and frozen_cmd[2] == request_cmd[2]
        and frozen_cmd[2].startswith(_REPORT_VALIDATOR_PREFIX)
        and frozen_cmd[2].endswith("))")
    )


def native_steward_client_id(actor: Mapping[str, object], turn_instance_id: str) -> str:
    """The original native request identity, shared by dispatch and readback."""
    if actor.get("schema_version") == "loopx_managed_session_actor_v0":
        return "managed." + turn_instance_id
    return "native." + hashlib.sha256(json.dumps([actor, turn_instance_id], sort_keys=True).encode()).hexdigest()[:40]


def report_turn(
    store: StewardStoreProtocol,
    actor: Mapping[str, object],
    window: Mapping[str, object],
) -> dict[str, object] | None:
    """Read the one bounded continuation, retaining the original failed Turn."""
    session_id = str(actor["session_id"])
    operation = "report." + str(window["window_id"])
    original = store.turn_for_client(session_id, native_steward_client_id(actor, operation))
    quota = store.turn_for_client(session_id, native_steward_client_id(actor, operation + ".quota-recovery"))
    timeout_retry = store.turn_for_client(session_id, native_steward_client_id(actor, operation + ".timeout-recovery"))
    if quota is not None and timeout_retry is not None:
        raise ValueError("conflicting report continuations exist")
    for suffix, key, label, continued in (
        (".quota-recovery", "report_quota_recovery", "quota", quota),
        (".timeout-recovery", "report_timeout_recovery", "timeout", timeout_retry),
    ):
        if continued is None:
            continue
        request = as_mapping(continued.get("loopx_request"))
        recovery = as_mapping(request.get(key))
        original_req = as_mapping((original or {}).get("loopx_request")) if original else {}
        if (
            not original
            or recovery.get("original_turn_id") != original.get("turn_id")
            or request.get("turn_instance_id") != str(original_req.get("turn_instance_id") or "") + suffix
        ):
            raise ValueError(f"the report {label} continuation changed its original identity")
        return continued
    return original


def normalize_report_kernel_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
    """Normalize a verified report candidate to canonical Turn settlement fields."""
    normalized = dict(candidate)
    normalized.pop("editorial_response", None)
    # This finite task's postcondition is the complete validated generation, not
    # the model's choice of progress wording or profile-minimum shorthand.
    normalized["result_kind"] = "validated_completion"
    if normalized.get("delivery_batch_scale") == "multi_surface_or_implementation":
        normalized["delivery_batch_scale"] = "multi_surface"
    return normalized


def split_evidence_text_chunks(text: str, max_item_chars: int) -> list[str]:
    """Split a single normalized evidence string cleanly at natural boundaries."""
    if len(text) <= max_item_chars:
        return [text]
    chunks: list[str] = []
    rest = text
    delimiters = ("; ", ". ", "；", "。", ", ", "，", " ")
    while len(rest) > max_item_chars:
        split_at = -1
        matched_delim = ""
        for delim in delimiters:
            pos = rest.rfind(delim, 1, max_item_chars)
            if pos > 0:
                split_at = pos
                matched_delim = delim
                break
        if split_at <= 0:
            chunks.append(rest[:max_item_chars])
            rest = rest[max_item_chars:].lstrip()
        elif matched_delim in ("；", "。", "，"):
            end = min(split_at + len(matched_delim), max_item_chars)
            chunks.append(rest[:end].strip())
            rest = rest[end:].lstrip()
        else:
            head = rest[:split_at].rstrip()
            if matched_delim == ". " and len(head) + 1 <= max_item_chars:
                head += "."
            chunks.append(head)
            rest = rest[split_at + len(matched_delim) :].lstrip()
    if rest:
        chunks.append(rest)
    return [chunk for chunk in chunks if chunk]


def append_bounded_ellipsis(text: str, limit: int) -> str:
    """Append '...' to text while respecting the exact character cap."""
    if limit <= 0:
        return ""
    if limit <= 3:
        return text[:limit]
    if len(text) + 3 <= limit and not text.endswith("..."):
        return text + "..."
    trimmed = text[: max(1, limit - 3)].rstrip()
    return (trimmed or text[: max(1, limit - 3)]) + "..."


def split_bounded_evidence_items(
    raw: object,
    *,
    max_items: int = 8,
    max_item_chars: int = 240,
    max_total_chars: int = 1_200,
) -> object:
    """Split over-long Steward evidence strings without masking invalid shapes."""
    if not isinstance(raw, list) or not raw:
        return raw
    items = cast(list[object], raw)
    if not all(isinstance(item, str) and bool(item.strip()) for item in items):
        return raw
    str_items = [cast(str, item) for item in items]
    chunks: list[str] = []
    for item in str_items:
        normalized = " ".join(item.split()).strip()
        chunks.extend(split_evidence_text_chunks(normalized, max_item_chars))
    if len(chunks) > max_items:
        merged: list[str] = []
        for chunk in chunks:
            if merged and len(merged[-1]) + 2 + len(chunk) <= max_item_chars:
                merged[-1] = f"{merged[-1]}; {chunk}"
            else:
                merged.append(chunk)
        chunks = merged
    clipped = len(chunks) > max_items
    if clipped:
        chunks = chunks[:max_items]
    bounded: list[str] = []
    used = 0
    for chunk in chunks:
        remaining = max_total_chars - used
        if remaining <= 0:
            clipped = True
            break
        if len(chunk) > remaining or len(chunk) > max_item_chars:
            cap = min(max_item_chars, remaining)
            bounded.append(append_bounded_ellipsis(chunk, cap))
            used += len(bounded[-1])
            clipped = True
            break
        bounded.append(chunk)
        used += len(chunk)
    if clipped and bounded and not bounded[-1].endswith("..."):
        room = min(max_item_chars, len(bounded[-1]) + (max_total_chars - used))
        bounded[-1] = append_bounded_ellipsis(bounded[-1], room)
    return bounded


def normalize_steward_advancement_kernel_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
    """Keep Steward advancement evidence within kernel per-item and total limits."""
    normalized = dict(candidate)
    if normalized.get("delivery_batch_scale") == "multi_surface_or_implementation":
        normalized["delivery_batch_scale"] = "multi_surface"
    for field in ("work_evidence", "validation_evidence"):
        if field in normalized:
            normalized[field] = split_bounded_evidence_items(normalized[field])
    return normalized


def is_zero_effect_replan_selection_deferral(result: Mapping[str, object]) -> bool:
    """Identify a zero-effect pre-host autonomous_replan selection refusal."""
    if result.get("ok") is not False or result.get("error_code") != "turn_todo_selection_deferred":
        return False
    refusal = result.get("selection_rejection")
    if (
        not isinstance(refusal, Mapping)
        or refusal.get("state") != "deferred"
        or refusal.get("reason_code") != "autonomous_replan"
        or refusal.get("recovery_action") != "reenter_guard_without_selection"
    ):
        return False
    effects = result.get("effects")
    if not isinstance(effects, Mapping):
        return False
    return all(
        effects.get(key) is False
        for key in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged")
    )
