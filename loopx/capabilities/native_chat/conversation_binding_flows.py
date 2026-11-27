"""Publication, resolution, discovery, and target-update flows for conversation bindings."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import uuid

from ...file_lock import exclusive_file_lock
from .conversation_binding_contracts import (
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingRowDict,
    BindingStateDict,
    CoreEffectFn,
    LockFn,
    ObserveAudienceFn,
    ObserveFn,
    ProjectContextsProtocol,
    ResolvedBindingDict,
    WriteJsonFn,
    as_binding_row,
    as_binding_state,
    session_observation,
)


def _require_projects(
    projects: ProjectContextsProtocol | None,
) -> ProjectContextsProtocol:
    if projects is None:
        raise ValueError("bound conversation project contexts are unavailable")
    return projects


def configure_binding_flow(
    *,
    bindings_path: Path,
    projects: ProjectContextsProtocol | None,
    transport_ref: str,
    project_ref: str,
    executor_endpoint_id: str,
    context_kind: str,
    project_grant: str | None,
    goal_scope: str | None,
    audience: str | None,
    group_refs: list[str] | None,
    available_group_refs: list[str] | None,
    expected_provider_ref: str | None,
    expected_operator_ref: str | None,
    read_fn: Callable[[], BindingStateDict],
    observe_fn: ObserveAudienceFn,
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    delivery_channels_fn: Callable[[BindingPayloadView], list[str]],
    ensure_delivery_scope_fn: Callable[..., None],
    revoke_delivery_sources_fn: Callable[[str], None],
    stop_revoked_binding_fn: Callable[[str], None],
    lock_fn: LockFn = exclusive_file_lock,
) -> BindingRowDict:
    if goal_scope is not None and (
        context_kind != "steward" or goal_scope not in {"selected", "all_registered"}
    ):
        raise ValueError("select a supported steward Goal scope")
    resolved_projects = _require_projects(projects)
    if project_grant is None:
        project_grant = (
            resolved_projects.workspace_grant
            if context_kind == "project" and executor_endpoint_id == "codex"
            else "workspace_read"
        )
    if project_grant not in {"workspace_read", "workspace_write"} or (
        context_kind != "project" and project_grant != "workspace_read"
    ):
        raise ValueError("workspace write authorization is only available for project Chat")
    observation = observe_fn(transport_ref, audience=audience or "owner")
    return _publish_configured_binding(
        bindings_path=bindings_path,
        projects=resolved_projects,
        transport_ref=transport_ref,
        project_ref=project_ref,
        executor_endpoint_id=executor_endpoint_id,
        context_kind=context_kind,
        project_grant=project_grant,
        goal_scope=goal_scope,
        audience=audience,
        group_refs=group_refs,
        available_group_refs=available_group_refs,
        expected_provider_ref=expected_provider_ref,
        expected_operator_ref=expected_operator_ref,
        observation=observation,
        read_fn=read_fn,
        core_fn=core_fn,
        write_json_fn=write_json_fn,
        delivery_channels_fn=delivery_channels_fn,
        ensure_delivery_scope_fn=ensure_delivery_scope_fn,
        revoke_delivery_sources_fn=revoke_delivery_sources_fn,
        stop_revoked_binding_fn=stop_revoked_binding_fn,
        lock_fn=lock_fn,
    )


def _publish_configured_binding(
    *,
    bindings_path: Path,
    projects: ProjectContextsProtocol,
    transport_ref: str,
    project_ref: str,
    executor_endpoint_id: str,
    context_kind: str,
    project_grant: str,
    goal_scope: str | None,
    audience: str | None,
    group_refs: list[str] | None,
    available_group_refs: list[str] | None,
    expected_provider_ref: str | None,
    expected_operator_ref: str | None,
    observation: BindingPayloadView,
    read_fn: Callable[[], BindingStateDict],
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    delivery_channels_fn: Callable[[BindingPayloadView], list[str]],
    ensure_delivery_scope_fn: Callable[..., None],
    revoke_delivery_sources_fn: Callable[[str], None],
    stop_revoked_binding_fn: Callable[[str], None],
    lock_fn: LockFn = exclusive_file_lock,
) -> BindingRowDict:
    if (
        expected_provider_ref is not None
        and observation["provider_ref"] != expected_provider_ref
    ):
        raise ValueError(
            "the selected App changed before binding publication; verify it again"
        )
    if (
        expected_operator_ref is not None
        and observation["operator_ref"] != expected_operator_ref
    ):
        raise ValueError(
            "the selected owner changed before binding publication; verify it again"
        )
    candidate: BindingPayload = {
        "schema_version": "loopx_chat_conversation_binding_v0",
        "binding_id": uuid.uuid4().hex[:24],
        "transport_ref": transport_ref,
        "provider_ref": observation["provider_ref"],
        "operator_ref": observation["operator_ref"],
        "context_kind": context_kind,
        "project_ref": project_ref,
        "executor_endpoint_id": executor_endpoint_id,
        "grant": project_grant if context_kind == "project" else "portfolio_read",
        "enabled": True,
        **(
            {"goal_ids": [], "goal_scope": goal_scope or "all_registered"}
            if context_kind == "steward"
            else {}
        ),
        **({"audience": audience} if audience is not None else {}),
        **({"group_refs": group_refs} if group_refs is not None else {}),
    }
    with lock_fn(
        bindings_path, operation="configure_chat_conversation_binding"
    ):
        current = read_fn()
        previous = next(
            (
                row
                for row in current["bindings"]
                if row["transport_ref"] == transport_ref
            ),
            None,
        )
        if previous and all(
            previous.get(key) == candidate.get(key)
            for key in (
                "context_kind",
                "project_ref",
                "executor_endpoint_id",
                "provider_ref",
                "operator_ref",
                "grant",
                "audience",
                "group_refs",
            )
        ):
            if previous.get("agent_targets"):
                candidate["agent_targets"] = previous["agent_targets"]
        if (
            context_kind == "steward"
            and previous
            and all(
                previous.get(key) == candidate.get(key)
                for key in (
                    "context_kind",
                    "project_ref",
                    "executor_endpoint_id",
                    "provider_ref",
                    "operator_ref",
                )
            )
        ):
            candidate["goal_ids"] = previous["goal_ids"]
            # Existing installations retain their exact audience and scope
            # until the local operator explicitly changes it.
            if goal_scope is None:
                candidate.pop("goal_scope", None)
                if "goal_scope" in previous:
                    candidate["goal_scope"] = previous["goal_scope"]
            candidate["binding_id"] = previous["binding_id"]
            if previous.get("managed_actor") is not None:
                candidate["managed_actor"] = previous["managed_actor"]
        result = core_fn(
            "collaboration.conversation.binding",
            {
                "current": current,
                "expected_revision": current.get("revision"),
                "operation": "configure",
                "binding": candidate,
                "observation": observation,
                "available_projects": projects.available(),
                **(
                    {"available_group_refs": available_group_refs}
                    if audience == "group"
                    else {}
                ),
            },
        )
        state_map = result["state"]
        assert isinstance(state_map, Mapping)
        raw_bindings = state_map["bindings"]
        assert isinstance(raw_bindings, list)
        proposed = next(
            row
            for row in raw_bindings
            if isinstance(row, Mapping) and row["transport_ref"] == transport_ref
        )
        channels = delivery_channels_fn(proposed) if goal_scope is not None else []
        for channel in channels:
            ensure_delivery_scope_fn(
                {"binding": proposed, "channel_id": channel}, execute=False
            )
        # Publish the advertised scope only after every known source policy
        # has applied and verified. On IO failure the old binding remains;
        # retrying the explicit scope operation converges any earlier source
        # writes without making ordinary admission undo a manual restriction.
        for channel in channels:
            ensure_delivery_scope_fn(
                {"binding": proposed, "channel_id": channel}, if_absent=False
            )
        if previous and previous["binding_id"] != proposed["binding_id"]:
            revoke_delivery_sources_fn(previous["binding_id"])
        if result["changed"] and isinstance(state_map, dict):
            write_json_fn(bindings_path, state_map)
        readback = read_fn()
        if readback != result["state"]:
            raise OSError("conversation binding publication did not verify")
        binding = next(
            row
            for row in readback["bindings"]
            if row["transport_ref"] == transport_ref
        )
    if previous and previous["binding_id"] != binding["binding_id"]:
        stop_revoked_binding_fn(previous["binding_id"])
    return as_binding_row(binding)


def disconnect_binding_flow(
    *,
    bindings_path: Path,
    binding_id: str,
    expected_revision: int,
    read_fn: Callable[[], BindingStateDict],
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    revoke_delivery_sources_fn: Callable[[str], None],
    stop_revoked_binding_fn: Callable[[str], None],
    lock_fn: LockFn = exclusive_file_lock,
) -> BindingStateDict:
    with lock_fn(
        bindings_path, operation="disconnect_chat_conversation_binding"
    ):
        result = core_fn(
            "collaboration.conversation.binding",
            {
                "current": read_fn(),
                "expected_revision": expected_revision,
                "operation": "disconnect",
                "binding_id": binding_id,
            },
        )
        revoke_delivery_sources_fn(binding_id)
        state_map = result["state"]
        if result["changed"] and isinstance(state_map, dict):
            write_json_fn(bindings_path, state_map)
        if read_fn() != result["state"]:
            raise OSError("conversation binding revocation did not verify")
    stop_revoked_binding_fn(binding_id)
    assert isinstance(state_map, Mapping)
    return as_binding_state(state_map)


def select_app_steward_flow(
    *,
    bindings_path: Path,
    binding_id: str | None,
    expected_revision: int,
    projects: ProjectContextsProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    observe_fn: ObserveFn,
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    lock_fn: LockFn = exclusive_file_lock,
) -> BindingStateDict:
    row = next(
        (item for item in read_fn()["bindings"] if item["binding_id"] == binding_id),
        None,
    )
    observation = observe_fn(row["transport_ref"]) if row is not None else None
    available = _require_projects(projects).available() if binding_id is not None else []
    with lock_fn(bindings_path, operation="select_app_steward"):
        result = core_fn(
            "collaboration.conversation.binding",
            {
                "current": read_fn(),
                "expected_revision": expected_revision,
                "operation": "select_app_steward",
                "binding_id": binding_id,
                "observation": observation,
                "available_projects": available,
            },
        )
        state_map = result["state"]
        if result["changed"] and isinstance(state_map, dict):
            write_json_fn(bindings_path, state_map)
        if read_fn() != result["state"]:
            raise OSError("App steward selection did not verify")
        assert isinstance(state_map, Mapping)
        return as_binding_state(state_map)


def find_binding_row(
    state: BindingPayloadView, binding_id: str
) -> BindingPayloadView | None:
    raw_bindings = state.get("bindings")
    rows = raw_bindings if isinstance(raw_bindings, list) else []
    return next(
        (
            item
            for item in rows
            if isinstance(item, Mapping) and item.get("binding_id") == binding_id
        ),
        None,
    )


def build_resolved_binding(
    *,
    current: BindingPayloadView,
    row: BindingPayloadView,
    observation: BindingPayload,
    resolve_observed_fn: Callable[
        [BindingPayloadView, BindingPayloadView, BindingPayloadView, BindingPayloadView],
        BindingPayload,
    ],
    source_ref: str,
    sender_ref: str,
    private_human_message: bool,
    session_context: BindingPayloadView | None,
    group_ref: str | None,
    topic_ref: str | None,
    group_human_message: bool,
    include_observation: bool,
    origin: str | None,
    session: BindingPayloadView | None,
) -> ResolvedBindingDict:
    selected = resolve_observed_fn(
        current,
        row,
        observation,
        {
            "source_ref": source_ref,
            "sender_ref": sender_ref,
            "private_human_message": private_human_message,
            **({"origin": origin} if origin is not None else {}),
            **(
                {"session_context": session_context}
                if session_context is not None
                else {}
            ),
            **({"session": session_observation(session)} if session is not None else {}),
            **({"group_ref": group_ref} if group_ref is not None else {}),
            **({"topic_ref": topic_ref} if topic_ref is not None else {}),
            **(
                {"group_human_message": group_human_message}
                if group_ref is not None or group_human_message
                else {}
            ),
        },
    )
    # Presentation may need the name from this exact verified read. Keep it
    # outside the persisted context and opt-in; it is never a cached grant
    # or a substitute for the next admission/delivery owner's fresh check.
    if include_observation:
        return ResolvedBindingDict({**selected, "observation": observation})
    return ResolvedBindingDict(selected)


def observe_local_sessions_flow(
    *,
    binding_id: str,
    app_steward_entry: bool,
    controller: BindingControllerProtocol | None,
    projects: ProjectContextsProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    observe_fn: ObserveFn,
    core_fn: CoreEffectFn,
    goal_scope_ids_fn: Callable[[BindingPayloadView], list[str]],
) -> tuple[BindingStateDict, list[BindingPayload], BindingPayload]:
    if controller is None:
        raise ValueError("the bound conversation store is unavailable")
    row = next(
        (item for item in read_fn()["bindings"] if item["binding_id"] == binding_id),
        None,
    )
    if row is None:
        raise ValueError("conversation binding is no longer authorized")
    observation = dict(observe_fn(row["transport_ref"]))
    store = controller.store

    def mentions_binding(session: BindingPayloadView) -> bool:
        return any(
            isinstance(ctx := session.get(key), dict)
            and ctx.get("binding_id") == binding_id
            for key in ("project_context", "steward_context")
        )

    # Keep aliases in the candidate set even when their own metadata is
    # unrelated. Core must still see duplicate canonical Session identities.
    hints = (
        session
        for path, session in store.iter_session_metadata()
        if path.parent.name != session["session_id"] or mentions_binding(session)
    )
    sessions: list[BindingPayload] = []
    for hint in sorted(
        hints, key=lambda item: str(item.get("updated_at") or ""), reverse=True
    ):
        session = store.load_session(str(hint["session_id"]))
        if session and mentions_binding(session):
            sessions.append(session)
    current = read_fn()
    if (
        next(
            (item for item in current["bindings"] if item["binding_id"] == binding_id),
            None,
        )
        != row
    ):
        raise ValueError("conversation binding changed during identity verification")
    available = _require_projects(projects).available()
    result = core_fn(
        "collaboration.conversation.local_sessions",
        {
            "current": current,
            "binding_id": binding_id,
            "observation": observation,
            "available_projects": available,
            "sessions": [session_observation(session) for session in sessions],
            **({"app_steward_entry": True} if app_steward_entry else {}),
            **(
                {"available_goal_ids": goal_scope_ids_fn(row)}
                if row.get("goal_scope") == "all_registered"
                else {}
            ),
        },
    )
    by_id = {str(session["session_id"]): session for session in sessions}
    project = next(
        project for project in available if project["project_ref"] == row["project_ref"]
    )
    source: BindingPayload = {
        "binding_id": binding_id,
        "app_ref": row["transport_ref"],
        "context_kind": row["context_kind"],
        "audience": row.get("audience", "owner"),
        "project_title": Path(str(project["workspace_path"])).name,
        "context_available": True,
        "executor_endpoint_id": row["executor_endpoint_id"],
    }
    raw_session_ids = result["session_ids"]
    assert isinstance(raw_session_ids, list)
    return current, [by_id[str(sid)] for sid in raw_session_ids], source


def adopt_created_goal_flow(
    *,
    bindings_path: Path,
    binding_id: str,
    source: BindingPayloadView,
    proposal: BindingPayloadView,
    goal: BindingPayloadView,
    resolve_source_fn: Callable[[str, BindingPayloadView], ResolvedBindingDict],
    read_fn: Callable[[], BindingStateDict],
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    lock_fn: LockFn = exclusive_file_lock,
) -> None:
    selected = resolve_source_fn(binding_id, source)
    with lock_fn(bindings_path, operation="adopt_steward_created_goal"):
        current = read_fn()
        result = core_fn(
            "collaboration.conversation.binding",
            {
                "current": current,
                "expected_revision": current["revision"],
                "operation": "adopt_created_goal",
                "binding_id": binding_id,
                "context": selected["context"],
                "proposal": proposal,
                "goal": {
                    "goal_id": goal["id"],
                    "workspace_path": str(Path(str(goal["repo"])).resolve()),
                    "creation_operation_id": goal.get("creation_operation_id"),
                },
            },
        )
        state_map = result["state"]
        if result["changed"] and isinstance(state_map, dict):
            write_json_fn(bindings_path, state_map)
        if read_fn() != result["state"]:
            raise OSError("steward scope publication did not verify")


def change_agent_target_flow(
    *,
    bindings_path: Path,
    binding_id: str,
    expected_revision: int,
    session_id: str | None,
    target_ref: str | None,
    controller: BindingControllerProtocol | None,
    projects: ProjectContextsProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    observe_fn: ObserveFn,
    observe_agent_fn: Callable[[str], BindingPayload],
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    lock_fn: LockFn = exclusive_file_lock,
) -> BindingStateDict:
    row = next(
        (item for item in read_fn()["bindings"] if item["binding_id"] == binding_id),
        None,
    )
    if row is None:
        raise ValueError("conversation binding is no longer authorized")
    # Provider IO must not prevent disconnect from publishing revocation.
    # Recheck the exact binding under its fence before using this observation.
    observation = observe_fn(row["transport_ref"])
    with lock_fn(
        bindings_path, operation="configure_conversation_agent_target"
    ):
        current = read_fn()
        if (
            next(
                (
                    item
                    for item in current["bindings"]
                    if item["binding_id"] == binding_id
                ),
                None,
            )
            != row
        ):
            raise ValueError("conversation binding changed during identity verification")
        params: BindingPayload = {
            "current": current,
            "expected_revision": expected_revision,
            "binding_id": binding_id,
            "observation": observation,
            "available_projects": _require_projects(projects).available(),
        }
        if session_id is not None:
            observed = observe_agent_fn(session_id)
            observed_session = observed["session"]
            assert isinstance(observed_session, Mapping)
            target = {
                key: observed_session.get(key)
                for key in (
                    "session_id",
                    "goal_id",
                    "goal_instance_id",
                    "agent_id",
                    "executor_endpoint_id",
                )
            }
            params.update(
                operation="grant_agent_target",
                target={
                    "target_ref": uuid.uuid4().hex[:24],
                    "host_ref": observed["host_ref"],
                    **target,
                },
                target_observation=observed,
            )
        else:
            params.update(operation="revoke_agent_target", target_ref=target_ref)
        result = core_fn("collaboration.conversation.binding", params)
        state_map = result["state"]
        if result["changed"] and isinstance(state_map, dict):
            if session_id is not None:
                assert controller is not None
                controller.store.update_session(
                    session_id, external_conversation_binding_id=binding_id
                )
            write_json_fn(bindings_path, state_map)
        if read_fn() != result["state"]:
            raise OSError("Agent target grant publication did not verify")
        assert isinstance(state_map, Mapping)
        return as_binding_state(state_map)
