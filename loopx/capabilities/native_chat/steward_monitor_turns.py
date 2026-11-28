"""Session/turn identity, retry classification, scope storage, and typed protocols."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, NoReturn, Protocol, overload

from ...chat_store import ChatSessionStore, _atomic_write_json
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.time import timestamp_age_at_least

TARGET = "steward-portfolio-watch"
FOLLOWUP_TARGET_PREFIX = "steward-monitor-followup:"
STEWARD_BACKGROUND_SESSION_PREFIX = "steward.background."
STEWARD_BACKGROUND_CHANNEL_PREFIX = "manager.external.steward.background."
ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS = 60
ADVANCEMENT_ADMISSION_RETRY_SUFFIX = ".admission-retry"
ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX = ".precommit-retry"
_MONITOR_TARGET_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


class StewardTodoRecord(dict[str, object]):
    """Typed dict container for Steward monitor and follow-up Todo items."""

    @overload
    def __getitem__(
        self,
        key: Literal[
            "todo_id",
            "target_key",
            "next_due_at",
            "status",
            "created_at",
            "updated_at",
            "note",
            "task_class",
            "claimed_by",
            "priority",
        ],
        /,
    ) -> str: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)

    @overload
    def get(
        self,
        key: Literal[
            "todo_id",
            "target_key",
            "next_due_at",
            "status",
            "created_at",
            "updated_at",
            "note",
            "task_class",
            "claimed_by",
            "priority",
            "result_hash",
            "text",
            "task_repository",
            "cadence",
            "action_kind",
        ],
        default: str | None = None,
        /,
    ) -> str | None: ...

    @overload
    def get(
        self, key: Literal["resume_ready"], default: bool | None = None, /
    ) -> bool | None: ...

    @overload
    def get(self, key: str, default: object = None, /) -> object: ...

    def get(self, key: str, default: object = None, /) -> object:
        return super().get(key, default)


class StewardTurnRecord(dict[str, object]):
    """Typed dict container for persisted Steward Chat turn records."""

    @overload
    def __getitem__(self, key: Literal["session_id", "turn_id"], /) -> str: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)

    @overload
    def get(
        self,
        key: Literal["loopx_request", "response"],
        default: dict[str, object] | None = None,
        /,
    ) -> dict[str, object] | None: ...

    @overload
    def get(
        self,
        key: Literal["status", "error_code", "error", "completed_at"],
        default: str | None = None,
        /,
    ) -> str | None: ...

    @overload
    def get(self, key: str, default: object = None, /) -> object: ...

    def get(self, key: str, default: object = None, /) -> object:
        return super().get(key, default)


class StewardSessionStoreProtocol(Protocol):
    def _locked_session_path(
        self, session_id: str, operation: str
    ) -> AbstractContextManager[Path]: ...

    def create_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        adapter_kind: str,
        upstream_thread_id: str,
        channel_id: str | None = None,
        session_id: str | None = None,
        executor_endpoint_id: str | None = None,
        steward_context: None = None,
    ) -> object: ...

    def load_session(self, session_id: str) -> Mapping[str, object] | None: ...

    def turn_for_client(
        self, session_id: str, client_turn_id: str
    ) -> Mapping[str, object] | None: ...

    def load_turn(
        self, session_id: str, turn_id: str
    ) -> Mapping[str, object] | None: ...

    def queued_turns(self, session_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_sessions(self) -> Sequence[Mapping[str, object]]: ...

    def append_event(
        self,
        session_id: str,
        turn_id: str,
        *,
        kind: str,
        payload: dict[str, object],
    ) -> object: ...


class StewardBindingsProtocol(Protocol):
    path: Path

    def resolve(
        self,
        *,
        binding_id: str,
        session: dict[str, object],
        include_observation: bool,
        origin: str,
        source_ref: object,
        sender_ref: object,
        private_human_message: bool,
        session_context: object,
    ) -> dict[str, object]: ...

    def read(self) -> Mapping[str, object]: ...

    def _core(self, command: str, payload: dict[str, object]) -> dict[str, object]: ...

    def steward_scope(self, session: dict[str, object]) -> list[str] | None: ...


class StewardProjectContextsProtocol(Protocol):
    conversation_bindings: StewardBindingsProtocol


class StewardClosedSignalProtocol(Protocol):
    def is_set(self) -> bool: ...


class StewardControllerProtocol(Protocol):
    registry_path: Path
    coordination_runtime_root: Path
    store: ChatSessionStore
    lock: AbstractContextManager[object]
    closed: StewardClosedSignalProtocol
    project_contexts: StewardProjectContextsProtocol


class StewardHostProtocol(Protocol):
    def _observe(self, actor: Mapping[str, object]) -> dict[str, object] | None: ...

    def _admit(
        self,
        request: dict[str, object],
        *,
        turn: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
    ) -> Mapping[str, object]: ...


class StewardMonitorProtocol(Protocol):
    def todos(self, actor: Mapping[str, object]) -> list[StewardTodoRecord]: ...

    def due_monitors(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> list[StewardTodoRecord]: ...

    def runnable_advancement_todos(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> list[StewardTodoRecord]: ...


class StewardReadTodosFn(Protocol):
    def __call__(self, actor: Mapping[str, object]) -> list[StewardTodoRecord]: ...


class StewardAcquireLeaseFn(Protocol):
    def __call__(
        self,
        actor: Mapping[str, object],
        todo: Mapping[str, object],
        key: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, object]: ...


class StewardReleaseLeaseFn(Protocol):
    def __call__(
        self,
        actor: Mapping[str, object],
        todo: Mapping[str, object],
        lease: Mapping[str, object],
    ) -> None: ...


class StewardNativeCommandFn(Protocol):
    def __call__(
        self, controller: StewardControllerProtocol, argv: list[str]
    ) -> dict[str, object]: ...


def _coerce_turn_record(raw: Mapping[str, object] | None) -> StewardTurnRecord | None:
    if not isinstance(raw, Mapping):
        return None
    return StewardTurnRecord({str(k): v for k, v in raw.items()})


def _coerce_dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): v for k, v in raw.items()}


def steward_background_session_id(binding_id: str) -> str:
    return f"{STEWARD_BACKGROUND_SESSION_PREFIX}{binding_id}"


def steward_background_channel_id(binding_id: str) -> str:
    return f"{STEWARD_BACKGROUND_CHANNEL_PREFIX}{binding_id}"


def actor_steward_binding_id(actor: Mapping[str, object]) -> str | None:
    if actor.get("schema_version") != "loopx_steward_managed_actor_v0":
        return None
    goal_id = str(actor.get("goal_id") or "")
    if not goal_id.startswith("steward-agent-"):
        return None
    return goal_id.removeprefix("steward-agent-") or None


def lookup_steward_monitor_turn(
    store: StewardSessionStoreProtocol,
    binding_id: str | None,
    fallback_session_id: str,
    client_id: str,
) -> StewardTurnRecord | None:
    if binding_id is not None:
        bg_sid = steward_background_session_id(binding_id)
        if store.load_session(bg_sid) is not None:
            found = _coerce_turn_record(store.turn_for_client(bg_sid, client_id))
            if found is not None:
                return found
    return _coerce_turn_record(store.turn_for_client(fallback_session_id, client_id))


def load_steward_monitor_turn_by_id(
    store: StewardSessionStoreProtocol,
    actor: Mapping[str, object],
    turn_id: str,
) -> StewardTurnRecord | None:
    binding_id = actor_steward_binding_id(actor)
    if binding_id is not None:
        bg_sid = steward_background_session_id(binding_id)
        if store.load_session(bg_sid) is not None:
            found = _coerce_turn_record(store.load_turn(bg_sid, turn_id))
            if found is not None:
                return found
    return _coerce_turn_record(store.load_turn(str(actor["session_id"]), turn_id))


def ensure_steward_background_session(
    store: StewardSessionStoreProtocol,
    binding_id: str,
    parent_session: Mapping[str, object],
) -> dict[str, object]:
    from ...chat_store import utc_now

    saved = parent_session.get("steward_context")
    if not isinstance(saved, Mapping):
        raise ValueError("the original steward Session is unavailable")
    bg_sid = steward_background_session_id(binding_id)
    if store.load_session(bg_sid) is None:
        raw_endpoint = parent_session.get("executor_endpoint_id")
        try:
            store.create_session(
                session_id=bg_sid,
                goal_id="loopx-manager",
                agent_id=str(parent_session["agent_id"]),
                adapter_kind=str(parent_session["adapter_kind"]),
                upstream_thread_id=f"steward-watch-{binding_id}",
                channel_id=steward_background_channel_id(binding_id),
                executor_endpoint_id=(
                    raw_endpoint if isinstance(raw_endpoint, str) else None
                ),
                steward_context=None,
            )
        except FileExistsError:
            pass
    parent_scope_id = parent_session.get("manager_authorization_scope_id")
    expected_bg_context = {str(k): v for k, v in saved.items()}
    with store._locked_session_path(
        bg_sid, "ensure_steward_background_session"
    ) as path:
        loaded = store.load_session(bg_sid)
        if loaded is None or loaded.get("status") == "closed":
            raise ValueError("the steward background watch Session is unavailable")
        carrier = {str(k): v for k, v in loaded.items()}
        dirty = False
        if carrier.get("steward_background_context") != expected_bg_context:
            carrier["steward_background_context"] = expected_bg_context
            dirty = True
        if (
            isinstance(parent_scope_id, str)
            and parent_scope_id
            and carrier.get("manager_authorization_scope_id") != parent_scope_id
        ):
            carrier["manager_authorization_scope_id"] = parent_scope_id
            dirty = True
        elif parent_scope_id is None and "manager_authorization_scope_id" in carrier:
            carrier.pop("manager_authorization_scope_id", None)
            dirty = True
        if dirty:
            carrier["updated_at"] = utc_now()
            _atomic_write_json(path, carrier, preserve_mode=True)
        return carrier


class StewardObservationAdapter:
    """A watch reads native state, never an old model context or a new thread."""

    observation_only = True

    def __init__(self, upstream_thread_id: str | None) -> None:
        self.upstream_thread_id = upstream_thread_id

    def healthcheck(self) -> bool:
        return True

    def close_session(self) -> None:
        pass

    def interrupt_turn(self) -> None:
        pass

    def start_turn(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise ValueError("native watch observations do not invoke a model")


def monitor_turn_id(todo: Mapping[str, object]) -> str:
    # The native deadline, not a wall-clock tick, names this attempt. Restart
    # observes its original Core request; it never replays an unknown attempt.
    return (
        "monitor."
        + hashlib.sha256(
            json.dumps([todo["todo_id"], todo["next_due_at"]], sort_keys=True).encode()
        ).hexdigest()[:40]
    )


def advancement_turn_id(todo: Mapping[str, object]) -> str:
    """Stable Turn instance identity per Todo revision for Steward follow-ups."""
    stamp = todo.get("updated_at") or todo.get("created_at") or "0"
    note = str(todo.get("note") or "")
    basis: list[object] = [todo["todo_id"], stamp]
    if note.startswith("rearm-followup:"):
        basis.append(note)
    return (
        "advancement."
        + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:40]
    )


_PRE_DISPATCH_ADVANCEMENT_ERROR_CODES = frozenset({
    "resume_failed", "runtime_unavailable", "model_auth_unavailable",
})


def _past_precommit_cooldown(turn: Mapping[str, object], now: float | None) -> bool:
    """Return True when a finished turn has passed the precommit retry cooldown."""
    return timestamp_age_at_least(
        turn.get("completed_at"), ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS, now=now,
    )


def _zero_write_advancement_response(
    turn: Mapping[str, object], *, now: float | None = None,
) -> Mapping[str, object] | None:
    """Return native_loopx payload only when a completed Turn wrote zero Steward outputs and passed cooldown."""
    if turn.get("status") != "completed":
        return None
    resp = turn.get("response")
    if not isinstance(resp, Mapping):
        return None
    if resp.get("steward_owner_report_queued") or resp.get("context_handoff") or resp.get("steward_user_action_receipts"):
        return None
    nl = resp.get("native_loopx")
    if not isinstance(nl, Mapping) or nl.get("ok") is not False:
        return None
    return nl if _past_precommit_cooldown(turn, now) else None


def _is_zero_effect_pre_dispatch_failed_advancement_turn(
    turn: Mapping[str, object], *, now: float | None = None,
) -> bool:
    """Allow bounded retry only for unstarted outer Chat preparation failures before worker launch."""
    return (
        turn.get("status") == "failed"
        and turn.get("origin") == "native_loopx"
        and turn.get("loopx_execution") is True
        and turn.get("error_code") in _PRE_DISPATCH_ADVANCEMENT_ERROR_CODES
        and all(turn.get(k) is None for k in ("response", "upstream_turn_id", "started_at", "first_event_at"))
        and turn.get("delta_count") == 0
        and _past_precommit_cooldown(turn, now)
    )


def _is_precommit_retryable_advancement_turn(
    turn: Mapping[str, object], *, now: float | None = None,
) -> bool:
    """Allow one delayed retry only when zero kernel/Steward writes occurred."""
    if _is_zero_effect_pre_dispatch_failed_advancement_turn(turn, now=now):
        return True
    nl = _zero_write_advancement_response(turn, now=now)
    if nl is None:
        return False
    effects = nl.get("effects")
    if nl.get("error_code") == "turn_todo_selection_deferred":
        refusal = nl.get("selection_rejection")
        return (
            isinstance(refusal, Mapping) and refusal.get("state") == "deferred"
            and refusal.get("reason_code") == "autonomous_replan"
            and isinstance(effects, Mapping) and effects.get("host_invoked") is True
            and all(effects.get(k) is False for k in ("state_written", "quota_spent", "scheduler_acknowledged"))
        )
    if nl.get("status") != "failed" or nl.get("result_kind") not in {"validation_failed", "host_failure"}:
        return False
    receipt = nl.get("receipt")
    elig = (receipt.get("commit_eligibility") if isinstance(receipt, Mapping) else None) or {}
    eff = effects or {}
    if not isinstance(elig, Mapping) or not isinstance(eff, Mapping):
        return False
    return (elig.get("writeback") is False or eff.get("state_written") is False) and not any(
        eff.get(k) is True for k in ("state_written", "quota_spent", "scheduler_acknowledged")
    )


def _is_zero_effect_selection_deferred_advancement_turn(
    turn: Mapping[str, object], *, now: float | None = None,
) -> bool:
    """Allow bounded retry only for zero-effect pre-host selection deferrals."""
    nl = _zero_write_advancement_response(turn, now=now)
    refusal = nl.get("selection_rejection") if nl is not None else None
    effects = nl.get("effects") if nl is not None else None
    return (
        nl is not None and nl.get("error_code") == "turn_todo_selection_deferred"
        and isinstance(refusal, Mapping) and refusal.get("state") == "deferred"
        and isinstance(effects, Mapping)
        and not any(effects.get(k) is not False for k in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"))
    )


def _cached_turn_lookup(
    store: StewardSessionStoreProtocol,
    actor: Mapping[str, object],
    token: str,
    cache: dict[str, Mapping[str, object] | None],
    client_id_fn: Callable[[Mapping[str, object], str], str],
) -> Mapping[str, object] | None:
    if token not in cache:
        cache[token] = store.turn_for_client(
            str(actor["session_id"]), client_id_fn(actor, token)
        )
    return cache[token]


def _resolve_advancement_turn_id_with_cache(
    store: StewardSessionStoreProtocol,
    actor: Mapping[str, object],
    todo: Mapping[str, object],
    *,
    now: float | None = None,
    cache: dict[str, Mapping[str, object] | None],
) -> str | None:
    from .steward_turn import native_steward_client_id

    base = advancement_turn_id(todo)
    existing = _cached_turn_lookup(
        store, actor, base, cache, native_steward_client_id
    )
    if existing is None:
        return base
    primary_token = base
    if _is_zero_effect_selection_deferred_advancement_turn(existing, now=now):
        primary_token = base + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
        existing = _cached_turn_lookup(
            store, actor, primary_token, cache, native_steward_client_id
        )
        if existing is None:
            return primary_token
    if not _is_precommit_retryable_advancement_turn(existing, now=now):
        return None
    retry_token = primary_token + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    retry_existing = _cached_turn_lookup(
        store, actor, retry_token, cache, native_steward_client_id
    )
    if retry_existing is None:
        return retry_token
    if _is_zero_effect_selection_deferred_advancement_turn(retry_existing, now=now):
        retry_admission_token = retry_token + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
        if (
            _cached_turn_lookup(
                store, actor, retry_admission_token, cache, native_steward_client_id
            )
            is None
        ):
            return retry_admission_token
    return None


def resolve_advancement_turn_id(
    store: StewardSessionStoreProtocol,
    actor: Mapping[str, object],
    todo: Mapping[str, object],
    *,
    now: float | None = None,
) -> str | None:
    """Resolve the active attempt id or bounded post-cooldown deferral/pre-commit retries."""
    return _resolve_advancement_turn_id_with_cache(
        store, actor, todo, now=now, cache={}
    )


def is_exhausted_zero_write_followup_todo(
    store: StewardSessionStoreProtocol,
    actor: Mapping[str, object],
    todo: Mapping[str, object],
    *,
    now: float | None = None,
) -> bool:
    """Return True when an open monitor follow-up exhausted its per-revision retries with zero writes."""
    from .steward_turn import native_steward_client_id

    if (
        todo.get("status") != "open"
        or todo.get("task_class") != "advancement_task"
        or not str(todo.get("target_key") or "").startswith(FOLLOWUP_TARGET_PREFIX)
    ):
        return False
    cache: dict[str, Mapping[str, object] | None] = {}
    if (
        _resolve_advancement_turn_id_with_cache(
            store, actor, todo, now=now, cache=cache
        )
        is not None
    ):
        return False
    base = advancement_turn_id(todo)
    suffixes = (
        "",
        ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        ADVANCEMENT_ADMISSION_RETRY_SUFFIX + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX + ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
    )
    found_any = False
    for suffix in suffixes:
        turn = _cached_turn_lookup(
            store, actor, base + suffix, cache, native_steward_client_id
        )
        if turn is None:
            continue
        found_any = True
        if not (
            _is_precommit_retryable_advancement_turn(turn, now=now)
            or _is_zero_effect_selection_deferred_advancement_turn(turn, now=now)
        ):
            return False
    return found_any


def steward_monitor_snapshot_path(
    runtime_root: Path, goal_id: str, todo_id: str
) -> Path:
    return (
        Path(runtime_root).expanduser().resolve()
        / "steward-monitors"
        / goal_id
        / f"{todo_id}.latest.json"
    )


def steward_monitor_scope_path(
    runtime_root: Path, goal_id: str, target_key: str
) -> Path:
    safe_goal = str(goal_id or "").strip()
    safe_key = str(target_key or "").strip()
    if not safe_goal or "/" in safe_goal or "\\" in safe_goal or ".." in safe_goal:
        raise ValueError("invalid monitor scope goal_id")
    if not _MONITOR_TARGET_KEY_RE.fullmatch(safe_key):
        raise ValueError("invalid monitor scope target_key")
    return (
        Path(runtime_root).expanduser().resolve()
        / "steward-monitors"
        / safe_goal
        / "scopes"
        / f"{safe_key}.json"
    )


def steward_monitor_delivery_dir(runtime_root: Path, goal_id: str) -> Path:
    safe_goal = str(goal_id or "").strip()
    if not safe_goal or "/" in safe_goal or "\\" in safe_goal or ".." in safe_goal:
        raise ValueError("invalid monitor delivery goal_id")
    return (
        Path(runtime_root).expanduser().resolve()
        / "steward-monitors"
        / safe_goal
        / "deliveries"
    )


def normalize_steward_monitor_scope(
    signal_scope: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if signal_scope is None:
        return None
    normalized = effect_runtime_result(
        "collaboration.steward.monitor.signal_scope.normalize",
        {"signal_scope": {str(k): v for k, v in signal_scope.items()}},
    ).get("signal_scope")
    if not isinstance(normalized, Mapping):
        return None
    return {str(k): v for k, v in normalized.items()}


def write_steward_monitor_scope(
    runtime_root: Path,
    goal_id: str,
    target_key: str,
    signal_scope: Mapping[str, object],
) -> dict[str, object]:
    normalized = normalize_steward_monitor_scope(signal_scope)
    if normalized is None:
        raise ValueError("signal_scope cannot be empty")
    path = steward_monitor_scope_path(runtime_root, goal_id, target_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, normalized)
    return normalized


def read_steward_monitor_scope(
    runtime_root: Path, goal_id: str, target_key: str
) -> dict[str, object] | None:
    if not target_key or not _MONITOR_TARGET_KEY_RE.fullmatch(str(target_key)):
        return None
    path = steward_monitor_scope_path(runtime_root, goal_id, target_key)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("invalid persisted steward monitor scope")
    return normalize_steward_monitor_scope(raw)


_GOVERNANCE_TASK_CONTEXT_KEYS = frozenset({
    "goal_id",
    "agent_identity",
    "execution_profile",
    "task_scope",
    "mode",
    "state",
})


def governance_turn_prompt_authority(authority: Mapping[str, object]) -> dict[str, object]:
    """Project Turn authority to governance-relevant fields without mutating authority."""
    compact = dict(authority)
    compact.pop("vision_continuation_audit", None)
    model_input = compact.get("_model_input")
    if isinstance(model_input, Mapping):
        trimmed_input = dict(model_input)
        task_context = trimmed_input.get("task_context")
        if isinstance(task_context, Mapping):
            trimmed_input["task_context"] = {
                k: v for k, v in task_context.items() if k in _GOVERNANCE_TASK_CONTEXT_KEYS
            }
        compact["_model_input"] = trimmed_input
    return compact
