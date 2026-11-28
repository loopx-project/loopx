"""Management, grant configuration, and recipient discovery views for peer requests."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from ...control_plane.collaboration.goal_instance_scope import CollaborationGoalScope
from ...file_lock import exclusive_file_lock
from .conversation_binding_contracts import (
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingRowDict,
    CoreEffectFn,
    InspectResultFn,
    ReplyStatusFn,
)


def as_string_dict(value: object) -> dict[str, str] | None:
    """Return a string-keyed string dict or ``None`` when any value is non-str."""
    if not isinstance(value, Mapping):
        return None
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        result[key] = item
    return result


def configure_original_grant_flow(
    *,
    root: Path,
    session_id: str,
    body: BindingPayloadView,
    policy_fn: Callable[[], BindingPayload],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    from ...control_plane.collaboration.inbox import _read, _root, _write
    from ...control_plane.collaboration.source_policy_fence import source_policy_fence

    enable = body.get("grant")
    if not isinstance(enable, bool):
        raise ValueError("grant must be an explicit boolean")

    def apply(facts: BindingPayloadView) -> BindingPayload:
        plan = core_fn(
            "collaboration.source.original_conversation_grant",
            {
                **facts,
                "grant": enable,
                "expected_policy_sha256": body.get("expected_policy_sha256"),
            },
        )
        path = _root(root) / "policy.json"
        policy_doc = plan["policy"]
        if (
            body.get("execute") is True
            and plan["would_change"]
            and isinstance(policy_doc, dict)
        ):
            _write(path, policy_doc)
            if _read(path) != policy_doc:
                raise OSError("original conversation source grant did not verify")
        return {
            "ok": True,
            "executed": body.get("execute") is True,
            **{key: value for key, value in plan.items() if key != "policy"},
        }

    raw_requester = body["requester"]
    if not isinstance(raw_requester, Mapping):
        raise ValueError("an exact original requester is required")
    parent_request_id = str(body["parent_request_id"])
    if enable:
        with fenced_fn(session_id, raw_requester, parent_request_id) as (facts, _):
            return apply(facts)
    # An owner may revoke a retained grant even while its provider or source
    # is unavailable.
    with source_policy_fence(_root(root) / "policy.json"):
        recipient = body["recipient"]
        if (
            not isinstance(recipient, Mapping)
            or recipient.get("session_id") != session_id
        ):
            raise ValueError("original conversation revocation scope mismatch")
        return apply(
            {
                "policy": policy_fn(),
                "recipient": recipient,
                "requester": raw_requester,
                "parent_request_id": parent_request_id,
                "original_source_id": body["source_id"],
            }
        )


def original_management_view_flow(
    *,
    root: Path,
    bindings_path: Path,
    controller: BindingControllerProtocol,
    session_id: str,
    search: str,
    cursor: str | None,
    policy_fn: Callable[[], BindingPayload],
    observation_fn: Callable[[str], tuple[BindingRowDict, BindingPayload]],
    recipient_fn: Callable[
        [str, BindingPayloadView, BindingPayloadView, BindingPayloadView],
        BindingPayload,
    ],
    entry_fn: Callable[[str, BindingPayloadView], BindingPayload],
    inbox_fn: Callable[..., BindingPayload],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    from ...control_plane.collaboration import inbox
    from ...control_plane.collaboration.inbox import _hash, _read, _root, result_paths
    from ...control_plane.collaboration.source_policy_fence import source_policy_fence

    reply_status_fn: ReplyStatusFn = inbox.reply_status
    inspect_result_fn: InspectResultFn = inbox.inspect_result_identity

    with source_policy_fence(_root(root) / "policy.json"):
        policy = policy_fn()
        view = core_fn(
            "collaboration.source.original_conversation_grants",
            {"policy": policy, "session_id": session_id},
        )
    raw_grants = view["grants"]
    if not isinstance(raw_grants, list):
        raise ValueError("invalid original conversation grants view")
    for grant in raw_grants:
        if not isinstance(grant, dict):
            continue
        requester = grant["requester"]
        address = {
            "goal_ref": requester["goal_ref"],
            "agent_id": requester["agent_id"],
        }
        try:
            parent = _read(
                _root(root)
                / "entries"
                / _hash(address)
                / (str(grant["parent_request_id"]) + ".json")
            )
            if (
                parent.get("goal_ref") != requester["goal_ref"]
                or parent.get("agent_id") != requester["agent_id"]
            ):
                raise ValueError("source grant parent identity changed")
            brief = parent.get("brief")
            purpose = (
                brief.get("purpose", parent.get("message", ""))
                if isinstance(brief, Mapping) and brief
                else parent.get("message", "")
            )
            grant["source_purpose"] = purpose
        except (ValueError, OSError):
            grant["source_purpose"] = None
    try:
        binding, observed = observation_fn(session_id)
        with exclusive_file_lock(
            bindings_path, operation="original_conversation_inventory"
        ):
            view.update(recipient_fn(session_id, binding, observed, policy), available=True)
    except (ValueError, KeyError, OSError) as exc:
        return {
            "ok": True,
            **view,
            "available": False,
            "error": str(exc),
            "candidates": [],
            "requests": [],
        }
    candidates: list[BindingPayload] = []
    unreadable = 0
    overflow = False
    # Content is projected only to the trusted local owner. A later grant
    # still rechecks the exact registration, parent lineage and source.
    for path in sorted((_root(root) / "entries").glob("*/*.json")):
        try:
            row = _read(path)
            if (
                row.get("schema_version") != "loopx_manager_context_entry_v2"
                or not row.get("goal_ref")
            ):
                continue
            brief = row.get("brief")
            purpose = (
                brief.get("purpose", row.get("message", ""))
                if isinstance(brief, Mapping) and brief
                else row.get("message", "")
            )
            goal_ref = row["goal_ref"]
            goal_id_str = (
                str(goal_ref["goal_id"]) if isinstance(goal_ref, Mapping) else ""
            )
            if (
                search.strip().casefold()
                not in " ".join(
                    (goal_id_str, str(row["agent_id"]), str(purpose))
                ).casefold()
            ):
                continue
            candidates.append(
                {
                    "requester": {"goal_ref": goal_ref, "agent_id": row["agent_id"]},
                    "parent_request_id": row["request_id"],
                    "purpose": purpose,
                }
            )
            if len(candidates) == 128:
                overflow = True
                break
        except (ValueError, OSError, KeyError):
            unreadable += 1
    view.update(
        candidates=candidates,
        coverage={"unreadable": unreadable, "overflow": overflow},
    )
    raw_recipient = view["recipient"]
    page = inbox_fn(
        raw_recipient if isinstance(raw_recipient, Mapping) else {},
        mark_read=False,
        cursor=cursor,
    )
    view["requests"] = page["items"]
    view["requests_has_more"] = page["has_more"]
    view["requests_next_cursor"] = page["next_cursor"]
    raw_requests = view["requests"]
    if not isinstance(raw_requests, list):
        return {"ok": True, **view}
    for item in raw_requests:
        if not isinstance(item, dict):
            continue
        item_recipient = item["recipient"]
        if not isinstance(item_recipient, Mapping):
            continue
        results: list[BindingPayload] = []
        statuses = {
            row.get("result_key", row["phase"]): row["status"]
            for row in reply_status_fn(root, item)
        }
        entry = entry_fn(str(item["request_id"]), item_recipient)
        queue = core_fn("collaboration.project.continuation", {"entry": entry})
        turn = controller.store.turn_for_client(
            session_id, str(queue["client_turn_id"])
        )
        item["queue_status"] = turn.get("status") if turn else None
        for path in result_paths(_root(root) / "replies" / str(item["request_id"])):
            reply = _read(path)
            if inspect_result_fn(reply, entry, path):
                results.append(
                    {
                        "result_key": path.stem,
                        "text": reply["text"],
                        "decision": reply["decision"],
                        "request_revision": reply.get("request_revision", 1),
                        "delivery_status": statuses.get(path.stem, "queued"),
                    }
                )
        item["returns"] = results
    return {"ok": True, **view}


def discover_original_recipients_flow(
    *,
    root: Path,
    controller: BindingControllerProtocol,
    requester: BindingPayloadView,
    parent_request_id: str,
    policy_fn: Callable[[], BindingPayload],
    source_fn: Callable[
        [BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    from ...control_plane.collaboration.inbox import _root
    from ...control_plane.collaboration.source_policy_fence import source_policy_fence

    with (
        source_policy_fence(_root(root) / "policy.json"),
        source_fn(requester, parent_request_id) as (_, _),
    ):
        grants = core_fn(
            "collaboration.source.original_conversation_grants",
            {"policy": policy_fn()},
        )["grants"]
    recipients: list[BindingPayload] = []
    unavailable = 0
    if not isinstance(grants, list):
        return {"ok": True, "recipients": recipients, "unavailable": unavailable}
    for grant in grants:
        if (
            not isinstance(grant, Mapping)
            or grant.get("requester") != requester
            or grant.get("parent_request_id") != parent_request_id
        ):
            continue
        grant_recipient = grant["recipient"]
        if not isinstance(grant_recipient, Mapping):
            unavailable += 1
            continue
        try:
            with fenced_fn(
                str(grant_recipient["session_id"]),
                requester,
                parent_request_id,
            ) as (facts, _):
                core_fn("collaboration.source.original_conversation_access", facts)
                facts_recipient = facts["recipient"]
                if facts_recipient != grant_recipient:
                    raise ValueError("original conversation recipient changed")
                assert isinstance(facts_recipient, Mapping)
                session = controller.store.load_session(
                    str(facts_recipient["session_id"])
                )
                title = (session or {}).get("title") or "Project conversation"
                recipients.append({"recipient": facts_recipient, "title": title})
        except (ValueError, KeyError, OSError):
            unavailable += 1
    return {"ok": True, "recipients": recipients, "unavailable": unavailable}


def control_original_request_flow(
    *,
    root: Path,
    controller: BindingControllerProtocol,
    body: BindingPayloadView,
    entry_fn: Callable[[str, BindingPayloadView], BindingPayload],
    fenced_fn: Callable[
        [str, BindingPayloadView, str],
        AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]],
    ],
    enqueue_fn: Callable[[BindingPayloadView], tuple[BindingPayload, bool]],
    resume_fn: Callable[[str, BindingPayloadView, bool], object],
    stop_turns_fn: Callable[[str, list[str]], BindingPayload | None],
    core_fn: CoreEffectFn,
) -> BindingPayload:
    from ...control_plane.collaboration.goal_instance_scope import (
        collaboration_goal_scope,
    )
    from ...control_plane.collaboration.inbox import (
        _hash,
        _now,
        _read,
        _request_lock,
        _root,
        _write,
    )
    from ...control_plane.collaboration.source_policy_fence import (
        source_policy_fence,
    )

    requester = body["requester"]
    recipient = body["recipient"]
    operation = body["control"]
    parameters = body["parameters"]
    if (
        not isinstance(requester, Mapping)
        or not isinstance(recipient, Mapping)
        or operation not in {"inspect", "revise", "cancel"}
        or not isinstance(parameters, dict)
    ):
        raise ValueError("unsupported original conversation source control")
    row = entry_fn(str(body["request_id"]), recipient)
    if row["requester"] != requester:
        raise ValueError("original requester identity changed")
    session_id = str(recipient["session_id"])
    fence: AbstractContextManager[tuple[BindingPayload, CollaborationGoalScope]]
    if operation == "revise":
        fence = fenced_fn(session_id, requester, str(row["parent_request_id"]))
    else:
        # Revocation or a closed recipient must not remove the original
        # source's ability to inspect/cancel its retained request.
        @contextmanager
        def retained_source() -> Iterator[tuple[BindingPayload, CollaborationGoalScope]]:
            if controller.registry_path is None:
                raise ValueError("original conversation registry is unavailable")
            raw_ref = requester["goal_ref"]
            caller_goal_ref = as_string_dict(raw_ref)
            goal_id_val = raw_ref.get("goal_id") if isinstance(raw_ref, Mapping) else None
            with (
                source_policy_fence(_root(root) / "policy.json"),
                collaboration_goal_scope(
                    controller.registry_path,
                    goal_id=str(goal_id_val or ""),
                    agents=(str(requester["agent_id"]),),
                    caller_goal_ref=caller_goal_ref,
                ) as scope,
            ):
                if not scope.exact:
                    raise ValueError("original requester exact instance is unavailable")
                yield {}, scope

        fence = retained_source()
    turn: BindingPayload | None = None
    created = False
    with (
        fence as (facts, _),
        _request_lock(root, str(row["request_id"]), None, Path(), force_exact=True),
    ):
        current = entry_fn(str(row["request_id"]), recipient)
        route = _read(_root(root) / "roundtrips" / (str(row["request_id"]) + ".json"))
        core_fn("collaboration.project.return_route", {"entry": current, "route": route})
        if operation == "revise":
            if recipient != facts["recipient"]:
                raise ValueError("original conversation recipient changed")
            core_fn("collaboration.source.original_conversation_access", facts)
        plan = core_fn(
            "collaboration.request.control",
            {
                "entry": current,
                "input": {**parameters, "operation": operation},
                "source": {
                    "actor": "source",
                    "actor_agent_id": requester["agent_id"],
                    "actor_requester": requester,
                    "recipient_active": operation == "revise",
                    "observed_at": _now(),
                },
            },
        )
        plan_entry = plan.get("entry")
        if plan["changed"]:
            assert isinstance(plan_entry, dict)
            _write(
                _root(root)
                / "entries"
                / _hash({"recipient": recipient})
                / (str(row["request_id"]) + ".json"),
                plan_entry,
            )
        if operation == "revise":
            assert isinstance(plan_entry, Mapping)
            turn, created = enqueue_fn(plan_entry)
    stop = None
    if operation == "revise":
        assert turn is not None
        resume_fn(session_id, turn, created)
    elif operation == "cancel":
        assert isinstance(plan_entry, Mapping)
        queue = core_fn("collaboration.project.continuation", {"entry": plan_entry})
        superseded = queue["superseded_client_turn_ids"]
        superseded_ids = (
            [str(item) for item in superseded] if isinstance(superseded, list) else []
        )
        stop = stop_turns_fn(session_id, [*superseded_ids, str(queue["client_turn_id"])])
    return {
        "ok": True,
        "request_control": plan["view"],
        "replayed": plan.get("replayed", False),
        "turn_stop": stop,
    }
