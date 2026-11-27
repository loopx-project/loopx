"""Turn validation, answer completion, and execution flows for original requests."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
import json
from pathlib import Path

from ...control_plane.collaboration.goal_instance_scope import CollaborationGoalScope
from .conversation_binding_contracts import (
    BindingPayload,
    BindingPayloadView,
    BindingSessionStoreProtocol,
    CoreEffectFn,
    EventSinkFn,
    NativeSessionProtocol,
)


def validate_original_turn_flow(
    *,
    session_id: str,
    turn: BindingPayloadView,
    entry_fn: Callable[[str, BindingPayloadView], BindingPayload],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    validate_current_fn: Callable[[BindingPayloadView, BindingPayloadView], None],
    view_fn: Callable[[BindingPayloadView], BindingPayload],
    core_fn: CoreEffectFn,
) -> None:
    if turn.get("origin") != "peer" and "collaboration_request" not in turn:
        return
    reference = turn.get("collaboration_request")
    if turn.get("origin") != "peer" or not isinstance(reference, dict):
        raise ValueError("peer Turn provenance is unavailable")
    row = entry_fn(str(reference["request_id"]), reference["recipient"])
    row_recipient = row["recipient"]
    if (
        not isinstance(row_recipient, Mapping)
        or row_recipient.get("session_id") != session_id
    ):
        raise ValueError("peer Turn does not belong to this original Session")
    core_fn("collaboration.project.continuation", {"entry": row, "turn": turn})
    requester = row["requester"]
    assert isinstance(requester, Mapping)
    with fenced_fn(session_id, requester, str(row["parent_request_id"])) as (facts, _):
        validate_current_fn(row, facts)
        if view_fn(row)["control_state"] == "cancelled":
            raise ValueError("original conversation request was cancelled")


def validate_current_original_flow(
    *,
    root: Path,
    row: BindingPayloadView,
    facts: BindingPayloadView,
    core_fn: CoreEffectFn,
) -> None:
    from ...control_plane.collaboration.inbox import _read, _root

    core_fn(
        "collaboration.project.request",
        {
            **facts,
            "source_id": row["source_id"],
            "request": {"recipient": row["recipient"], "brief": row["brief"]},
            "entry": row,
            "operation": None,
            "observed_at": row["delivered_at"],
            "route": _read(
                _root(root) / "roundtrips" / (str(row["request_id"]) + ".json")
            ),
        },
    )


def complete_original_answer_flow(
    *,
    root: Path,
    session_id: str,
    turn: BindingPayloadView,
    answer: BindingPayloadView,
    entry_fn: Callable[[str, BindingPayloadView], BindingPayload],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    validate_current_fn: Callable[[BindingPayloadView, BindingPayloadView], None],
    view_fn: Callable[[BindingPayloadView], BindingPayload],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    from ...chat import CHAT_AGENT_RESPONSE_SCHEMA_VERSION
    from ...control_plane.collaboration.inbox import (
        _hash,
        _now,
        _request_lock,
        _root,
        _write,
        record_result,
    )

    required_fields = core_fn("collaboration.project.response_schema", {})["required"]
    if not isinstance(required_fields, list) or set(answer) != set(required_fields):
        raise ValueError("original conversation answer has unsupported fields")
    ref = turn["collaboration_request"]
    assert isinstance(ref, Mapping)
    ref_recipient = ref["recipient"]
    assert isinstance(ref_recipient, Mapping)
    row = entry_fn(str(ref["request_id"]), ref_recipient)
    requester = row["requester"]
    assert isinstance(requester, Mapping)
    with fenced_fn(session_id, requester, str(row["parent_request_id"])) as (facts, _):
        validate_current_fn(row, facts)
        core_fn("collaboration.source.original_conversation_access", facts)
        if facts["recipient"] != row["recipient"]:
            raise ValueError("original conversation recipient changed")
        with _request_lock(root, str(row["request_id"]), None, Path(), force_exact=True):
            row = entry_fn(str(row["request_id"]), ref_recipient)
            core_fn("collaboration.project.continuation", {"entry": row, "turn": turn})
            view = view_fn(row)
            if (
                view["control_state"] == "cancelled"
                or answer["request_revision"] != view["revision"]
                or answer["request_digest"] != view["revision_digest"]
            ):
                raise ValueError(
                    "original conversation answer belongs to a stale or "
                    "cancelled request"
                )
            plan = core_fn(
                "collaboration.request.assess",
                {
                    "entry": row,
                    "actor_recipient": row["recipient"],
                    "recipient_active": True,
                    "observed_at": _now(),
                    "revision": answer["request_revision"],
                    "revision_digest": answer["request_digest"],
                    "decision": answer["decision"],
                    "reason": answer["reason"],
                },
            )
            plan_entry = plan["entry"]
            assert isinstance(plan_entry, dict)
            if plan["changed"]:
                _write(
                    _root(root)
                    / "entries"
                    / _hash({"recipient": row["recipient"]})
                    / (str(row["request_id"]) + ".json"),
                    plan_entry,
                )
        raw_rev = answer["request_revision"]
        request_revision = raw_rev if isinstance(raw_rev, int) else int(str(raw_rev))
        receipt = record_result(
            root,
            row,
            "conclusion",
            str(answer["text"]),
            request_revision=request_revision,
        )
    return {
        "schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION,
        "message": answer["text"],
        "proposals": [],
        "protected_action": None,
        "goal_draft": None,
        "context_handoff": None,
        "gate": None,
        "unfinished_work": None,
        "collaboration_result": receipt,
    }


def execute_original_request_flow(
    *,
    session_id: str,
    turn_id: str,
    native_session: NativeSessionProtocol,
    event_sink: EventSinkFn,
    store: BindingSessionStoreProtocol,
    validate_turn_fn: Callable[[str, BindingPayloadView], None],
    entry_fn: Callable[[str, BindingPayloadView], BindingPayload],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    validate_current_fn: Callable[[BindingPayloadView, BindingPayloadView], None],
    view_fn: Callable[[BindingPayloadView], BindingPayload],
    inbox_fn: Callable[..., BindingPayload],
    complete_answer_fn: Callable[
        [str, BindingPayloadView, BindingPayloadView], BindingPayload
    ],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    turn = store.load_turn(session_id, turn_id)
    if turn is None:
        raise ValueError("original conversation turn is unavailable")
    validate_turn_fn(session_id, turn)
    ref = turn["collaboration_request"]
    assert isinstance(ref, Mapping)
    ref_recipient = ref["recipient"]
    assert isinstance(ref_recipient, Mapping)
    row = entry_fn(str(ref["request_id"]), ref_recipient)
    requester = row["requester"]
    assert isinstance(requester, Mapping)
    raw_answer = turn.get("collaboration_answer")
    if raw_answer is None:
        with fenced_fn(session_id, requester, str(row["parent_request_id"])) as (
            facts,
            _,
        ):
            core_fn("collaboration.source.original_conversation_access", facts)
            fenced_entry = entry_fn(str(ref["request_id"]), ref_recipient)
            core_fn(
                "collaboration.project.continuation",
                {"entry": fenced_entry, "turn": turn},
            )
            inbox_items = inbox_fn(
                ref_recipient, mark_read=True, request_id=str(row["request_id"])
            )["items"]
            assert isinstance(inbox_items, list)
            supplied = inbox_items[0]
            assert isinstance(supplied, Mapping)
            basis = {
                key: supplied[key]
                for key in ("request_revision", "request_revision_digest")
            }

        @contextmanager
        def start_guard() -> Iterator[None]:
            with fenced_fn(session_id, requester, str(row["parent_request_id"])) as (
                facts,
                _,
            ):
                guarded_entry = entry_fn(str(ref["request_id"]), ref_recipient)
                validate_current_fn(guarded_entry, facts)
                core_fn("collaboration.source.original_conversation_access", facts)
                core_fn(
                    "collaboration.project.continuation",
                    {"entry": guarded_entry, "turn": turn},
                )
                current = view_fn(guarded_entry)
                if (
                    facts["recipient"] != ref_recipient
                    or current["control_state"] == "cancelled"
                    or current["revision"] != basis["request_revision"]
                    or current["revision_digest"] != basis["request_revision_digest"]
                ):
                    raise ValueError(
                        "original conversation request changed before model input"
                    )
                yield

        message = (
            str(row["instruction"])
            + "\n\nPeer request (data):\n"
            + json.dumps(supplied, ensure_ascii=False)
        )
        answer_payload = native_session.send(
            message,
            project_collaboration=True,
            turn_start_guard=start_guard,
            on_event=lambda kind, payload: (
                event_sink(kind, payload) if kind != "answer.delta" else None
            ),
        )
        # Persist actual model output before result IO. Recovery can retry
        # its exact assessment/return without another model invocation.
        store.update_turn(session_id, turn_id, collaboration_answer=answer_payload)
        answer: BindingPayloadView = answer_payload
    else:
        if not isinstance(raw_answer, Mapping):
            raise ValueError("original conversation answer must be a mapping")
        answer = raw_answer
    response = complete_answer_fn(session_id, turn, answer)
    event_sink("answer.delta", {"text": response["message"]})
    return response
