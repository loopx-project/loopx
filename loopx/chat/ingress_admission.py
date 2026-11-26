"""External conversation admission and runtime activation checks for Chat ingress."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
from pathlib import Path
from typing import cast

from .ingress_common import (
    ConversationBindingsProtocol,
    LockFileFn,
    NONTERMINAL_TURN_STATES,
    as_mapping,
    is_valid_turn_record,
)


def resolve_external_admission_source(
    bindings: ConversationBindingsProtocol,
    binding_id: str,
    source: Mapping[str, object],
    source_preflight: Callable[[dict[str, object]], None] | None,
    *,
    session: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Resolve and optionally preflight-verify the authoritative binding for an inbound source."""
    observation_args: dict[str, object] = {**source}
    if source_preflight is not None:
        observation_args["include_observation"] = True
    if session is not None:
        observation_args.update(session=session, session_context=session["steward_context"])
    resolve_fn = cast(Callable[..., dict[str, object]], bindings.resolve)
    selected = resolve_fn(binding_id=binding_id, **observation_args)
    if source_preflight is not None and source_preflight(deepcopy(selected)) is not None:
        raise ValueError("source preflight must complete verification or raise")
    return selected


def ensure_external_admission_scope(
    bindings: ConversationBindingsProtocol,
    selected: dict[str, object],
    path: Path,
    request: dict[str, object],
    *,
    source_preflight: Callable[[dict[str, object]], None] | None,
    adoption_pending_code: str,
    lock_fn: LockFileFn,
) -> dict[str, object]:
    """Revalidate delivery scope and handle atomic steward executor adoption when needed."""
    source = as_mapping(request["source"])
    previous = as_mapping(selected["binding"])
    if (
        request["command"] is not None
        or request.get("agent_target") is not None
        or source.get("private_human_message") is not True
        or previous["context_kind"] != "steward"
        or previous.get("audience", "owner") != "owner"
        or previous.get("goal_scope") not in {"all_registered", "selected"}
        or bindings.controller is None
    ):
        bindings.ensure_delivery_scope(selected)
        return selected

    controller = bindings.controller
    with lock_fn(bindings.path, operation="configure_bound_delivery_scope"):
        raw_bindings = bindings.read().get("bindings")
        current = (
            next(
                (
                    binding
                    for binding in raw_bindings
                    if isinstance(binding, dict) and binding.get("binding_id") == previous["binding_id"]
                ),
                None,
            )
            if isinstance(raw_bindings, list)
            else None
        )
        if current == previous:
            bindings._ensure_delivery_scope(selected)  # This caller retains the binding fence.
            return selected
        if (
            current is None
            or {previous.get("executor_endpoint_id"), current.get("executor_endpoint_id")}
            != {"antigravity", "codex"}
            or current != {**previous, "executor_endpoint_id": current["executor_endpoint_id"]}
        ):
            raise ValueError("conversation binding is no longer authorized")
        endpoint = str(current["executor_endpoint_id"])
        raw_channel = selected["channel_id"]
        channel_id = raw_channel if isinstance(raw_channel, str) else None
        candidates = controller.store.resumable_session_candidates(
            goal_id="loopx-manager",
            agent_id=endpoint,
            channel_id=channel_id,
        )
        if len(candidates) != 1:
            raise ValueError("the original steward Session is unavailable for admission")
        original = candidates[0]
        session_id = original["session_id"]
        if (
            original.get("executor_endpoint_id") != endpoint
            or not isinstance(original.get("steward_context"), dict)
            or request.get("session_id") not in (None, session_id)
        ):
            raise ValueError("the original steward Session changed during admission")
        # Preserve the original Session across provider IO, service interruption
        # or Close. The existing prepared-request path owns subsequent admission.
        from ..chat_store import _atomic_write_json

        request.update(session_id=session_id, response_code=adoption_pending_code)
        _atomic_write_json(path, request)

    # No delivery effect has run. Reverify the same original source outside
    # the binding fence, then require the exact refreshed binding at the fence.
    fresh = resolve_external_admission_source(
        bindings,
        str(previous["binding_id"]),
        source,
        source_preflight,
        session=original,
    )
    if (
        fresh["binding"] != current
        or fresh["context"] != selected["context"]
        or fresh["channel_id"] != selected["channel_id"]
    ):
        raise ValueError("conversation binding changed during identity verification")
    bindings.ensure_delivery_scope(fresh)
    return fresh


def chat_runtime_activation_state(
    runtime_root: Path,
    *,
    active_session_ids: list[str] | None = None,
) -> dict[str, int]:
    """Strict read of all accepted work; never initialize or recover a store.

    The caller holds ``chat_runtime_activation_lock`` through the service switch.
    Session summaries alone omit queued input and are insufficient for this check.
    Unknown or unreadable records must not authorize stopping their consumer.
    When requested, collect Sessions with active pointers during this same scan.
    Their pointers only select recovery candidates. Turn records decide admission.
    """
    from ..chat_store import TERMINAL_TURN_STATES
    from ..control_plane.collaboration.source_chat_observation import (
        CHAT_SESSION_SCHEMA_VERSION,
        CHAT_TURN_SCHEMA_VERSION,
    )

    sessions = runtime_root.expanduser().resolve() / "chat" / "sessions"
    counts = {"sessions": 0, "nonterminal_turns": 0}
    if not sessions.exists():
        return counts
    valid_statuses = TERMINAL_TURN_STATES | NONTERMINAL_TURN_STATES
    for directory in sessions.iterdir():
        if not directory.is_dir():
            continue
        raw_session: object = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        if not isinstance(raw_session, dict) or raw_session.get("schema_version") != CHAT_SESSION_SCHEMA_VERSION:
            raise ValueError("unreadable Chat Session state")
        counts["sessions"] += 1
        active_id = raw_session.get("active_turn_id")
        active_found = not active_id
        turns = directory / "turns"
        for path in turns.iterdir() if turns.exists() else ():
            if path.suffix != ".json":
                continue
            raw_turn: object = json.loads(path.read_text(encoding="utf-8"))
            if not is_valid_turn_record(
                raw_turn,
                schema_version=CHAT_TURN_SCHEMA_VERSION,
                valid_statuses=valid_statuses,
            ):
                raise ValueError("unreadable Chat Turn state")
            if raw_turn.get("turn_id") == active_id:
                active_found = True
            if raw_turn["status"] not in TERMINAL_TURN_STATES:
                counts["nonterminal_turns"] += 1
        if not active_found:
            raise ValueError("active Chat Turn state is missing")
        if active_id and active_session_ids is not None:
            if raw_session.get("session_id") != directory.name:
                raise ValueError("active Chat Session identity does not match its directory")
            active_session_ids.append(directory.name)
    return counts
