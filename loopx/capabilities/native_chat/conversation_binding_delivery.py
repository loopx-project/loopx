"""Delivery scope, App steward entry, and input-admission context flows."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ...file_lock import exclusive_file_lock
from .conversation_binding_contracts import (
    AppStewardTargetDict,
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingSessionStoreProtocol,
    BindingStateDict,
    CoreEffectFn,
    LockFn,
    ReadJsonFn,
    ResolvedBindingDict,
    session_observation,
)


def app_steward_target_flow(
    app_steward_session_fn: Callable[[], BindingPayload | None],
) -> AppStewardTargetDict:
    session = app_steward_session_fn()
    if session is None:
        return AppStewardTargetDict({"binding_id": None, "session_id": None})
    steward_context = session["steward_context"]
    assert isinstance(steward_context, Mapping)
    return AppStewardTargetDict(
        {
            "binding_id": steward_context["binding_id"],
            "session_id": session["session_id"],
        }
    )


def app_steward_session_flow(
    *,
    controller: BindingControllerProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    core_fn: CoreEffectFn,
    observe_local_sessions_fn: Callable[
        [str, bool], tuple[BindingStateDict, list[BindingPayload], BindingPayload]
    ],
    recheck_local_bindings_fn: Callable[[BindingPayloadView], None],
) -> BindingPayload | None:
    current = read_fn()
    selected = current.get("app_steward_binding_id")
    if selected is None:
        # Validate legacy state through the same pure owner plan. An
        # unreadable grant must not masquerade as an unselected entry.
        core_fn(
            "collaboration.conversation.binding",
            {
                "current": current,
                "expected_revision": current.get("revision"),
                "operation": "select_app_steward",
                "binding_id": None,
            },
        )
        return None
    current, sessions, _ = observe_local_sessions_fn(str(selected), True)
    if not sessions:
        raise ValueError("the original steward Session is unavailable")
    session = max(
        sessions,
        key=lambda item: (
            str(item.get("last_activity_at") or ""),
            str(item["session_id"]),
        ),
    )
    if controller is None:
        raise ValueError("the bound conversation store is unavailable")
    # Close uses the same Session fences. Keep it from committing between
    # this read and the final grant decision; grant revocation still races
    # freely and is detected by the version check. All provider/Core IO has
    # already completed, and background grant inspection remains nonblocking.
    with controller.store.read_session_fenced(str(session["session_id"])) as fresh:
        if fresh is None or fresh.get("status") == "closed":
            raise ValueError("the original steward Session is unavailable")
        expected = {**session_observation(session), "status": fresh.get("status")}
        if session_observation(fresh) != expected:
            raise ValueError("the original steward Session is unavailable")
        recheck_local_bindings_fn(current)
        return fresh


@contextmanager
def input_context_flow(
    *,
    bindings_path: Path,
    saved: BindingPayloadView,
    origin: str,
    session: BindingPayloadView,
    store: BindingSessionStoreProtocol,
    session_source_fn: Callable[[BindingPayloadView], BindingPayload],
    resolve_source_fn: Callable[..., ResolvedBindingDict],
    read_fn: Callable[[], BindingStateDict],
    resolve_observed_fn: Callable[
        [BindingPayloadView, BindingPayloadView, BindingPayloadView, BindingPayloadView],
        BindingPayload,
    ],
    lock_fn: LockFn = exclusive_file_lock,
) -> Iterator[BindingPayload]:
    if origin == "web":
        source: BindingPayload = {
            "source_ref": saved["source_ref"],
            "sender_ref": saved["operator_ref"],
            "private_human_message": False,
            "origin": "web",
            "session_context": saved,
            **({"topic_ref": saved["topic_ref"]} if saved.get("topic_ref") else {}),
        }
    elif origin in {"lark", "external"}:
        source = session_source_fn(saved)
    else:
        raise ValueError("unsupported bound conversation input origin")
    observed = resolve_source_fn(
        binding_id=str(saved["binding_id"]),
        source=source,
        include_observation=True,
        session=session,
    )
    with lock_fn(bindings_path, operation="admit_bound_input"):
        current = read_fn()
        raw_bindings = current.get("bindings")
        binding_rows = raw_bindings if isinstance(raw_bindings, list) else []
        if (
            next(
                (
                    row
                    for row in binding_rows
                    if isinstance(row, Mapping)
                    and row.get("binding_id") == saved["binding_id"]
                ),
                None,
            )
            != observed["binding"]
        ):
            raise ValueError("conversation binding changed before input admission")
        # The provider observation may have waited. Re-read canonical
        # identity under the binding fence before Core authorizes input.
        fresh = store.load_session(str(session["session_id"]))
        if fresh is None:
            raise KeyError("chat session was not found")
        yield resolve_observed_fn(
            current,
            observed["binding"],
            observed["observation"],
            {**source, "session": session_observation(fresh)},
        )


def goal_scope_ids_flow(
    controller: BindingControllerProtocol | None,
    binding: BindingPayloadView,
) -> list[str]:
    if binding.get("goal_scope") != "all_registered":
        raw_goal_ids = binding.get("goal_ids", [])
        return (
            [str(item) for item in raw_goal_ids]
            if isinstance(raw_goal_ids, list)
            else []
        )
    from ...control_plane.projects.registry_codec import load_registry

    if controller is None or controller.registry_path is None:
        raise ValueError("the steward registry is unavailable")
    goals = load_registry(controller.registry_path).get("goals", [])
    return [str(goal["id"]) for goal in goals]


def delivery_channels_flow(
    controller: BindingControllerProtocol | None,
    binding: BindingPayloadView,
    *,
    read_json_fn: ReadJsonFn,
) -> list[str]:
    if controller is None:
        return []
    from ..manager_context import POLICY_SCHEMA, _root

    path = _root(controller.coordination_runtime_root) / "policy.json"
    if not path.exists():
        return []
    policy = read_json_fn(path)
    sources = policy.get("sources")
    if policy.get("schema_version") != POLICY_SCHEMA or not isinstance(sources, dict):
        raise ValueError("invalid manager policy")
    prefix = f"manager.external.native.{binding['binding_id']}."
    return [
        channel
        for channel, source in sources.items()
        if channel.startswith(prefix)
        and isinstance(source, dict)
        and binding["operator_ref"] in source.get("sender_ids", [])
    ]


def ensure_delivery_scope_flow(
    *,
    bindings_path: Path,
    controller: BindingControllerProtocol | None,
    selected: BindingPayloadView,
    if_absent: bool,
    execute: bool,
    read_fn: Callable[[], BindingStateDict],
    inner_ensure_fn: Callable[..., None],
    lock_fn: LockFn = exclusive_file_lock,
) -> None:
    raw_binding = selected["binding"]
    if (
        not isinstance(raw_binding, Mapping)
        or raw_binding.get("goal_scope") not in {"all_registered", "selected"}
        or controller is None
    ):
        return
    with lock_fn(bindings_path, operation="configure_bound_delivery_scope"):
        if raw_binding not in read_fn()["bindings"]:
            raise ValueError("conversation binding is no longer authorized")
        inner_ensure_fn(selected, if_absent=if_absent, execute=execute)


def apply_delivery_scope_flow(
    controller: BindingControllerProtocol | None,
    selected: BindingPayloadView,
    *,
    if_absent: bool,
    execute: bool,
) -> None:
    binding = selected["binding"]
    if (
        not isinstance(binding, Mapping)
        or binding.get("goal_scope") not in {"all_registered", "selected"}
        or controller is None
    ):
        return
    from ..manager_context import configure_delivery_scope

    configure_delivery_scope(
        controller.coordination_runtime_root,
        channel=str(selected["channel_id"]),
        local_delivery_scope=str(binding["goal_scope"]),
        sender_id=str(binding["operator_ref"]),
        execute=execute,
        if_absent=if_absent,
    )


def session_source(saved: BindingPayloadView) -> BindingPayload:
    if saved.get("audience") == "bound_group":
        return {
            "source_ref": saved["source_ref"],
            "sender_ref": saved["operator_ref"],
            "private_human_message": False,
            "group_ref": saved["group_ref"],
            "topic_ref": saved["topic_ref"],
            "group_human_message": True,
            "session_context": saved,
        }
    return {
        "source_ref": saved["source_ref"],
        "sender_ref": saved["operator_ref"],
        "private_human_message": True,
        "session_context": saved,
        **({"topic_ref": saved["topic_ref"]} if saved.get("topic_ref") else {}),
    }
