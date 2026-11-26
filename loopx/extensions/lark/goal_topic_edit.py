"""Resolve an existing Lark route without changing recipient or provider scope."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
    _configure_goal_with_global_sync_unlocked,
    resolve_configure_goal_sync_target,
)
from ...control_plane.projects.registry_codec import project_registry_transaction
from ...global_registry import GlobalRegistryReduction, mutate_global_registry
from ...history import load_registry
from ...registry import read_json, registry_goals
from .goal_channel_contracts import (
    binding_for_goal,
    bindings_for_goal,
    read_goal_channel_binding,
    operation_packet,
    write_goal_channel_binding,
)
from .goal_channel_targets import (
    goal_channel_target_for_name,
    read_goal_channel_targets,
)


@dataclass(frozen=True)
class ExistingGoalTopic:
    binding: dict[str, Any]
    app_ref: str
    chat_id: str
    chat_name: str
    agent_id: str | None
    capture_scope: str | None


def resolve_existing_goal_topic(
    *,
    goal: Mapping[str, Any],
    goal_id: str,
    binding_path: Path,
    target_path: Path,
    connection_id: str,
    app_ref: str,
    chat_id: str,
    agent_id: str | None,
    capture_scope: str | None,
) -> ExistingGoalTopic:
    """Caller holds the Goal binding mutation lock through validation and save."""
    editing = binding_for_goal(
        read_goal_channel_binding(binding_path),
        goal_id,
        connection_id=connection_id,
    )
    if not editing or editing.get("provider") != "lark" or not editing.get("enabled"):
        raise ValueError(
            "connection_id must name an enabled Lark connection for this Goal"
        )
    target = goal_channel_target_for_name(
        read_goal_channel_targets(target_path), str(editing.get("target_ref") or "")
    )
    if not target:
        raise ValueError("the existing connection target is unavailable")
    identity = target.get("identity") or {}
    channel = target.get("channel") or {}
    stored_app = str(identity.get("sender_profile") or "default")
    stored_chat = str(channel.get("chat_id") or "")
    if (app_ref and app_ref != stored_app) or (chat_id and chat_id != stored_chat):
        raise ValueError(
            "editing preserves the connection App and group; create a new connection to move it"
        )
    app_ref, chat_id = stored_app, stored_chat
    chat_name = str(channel.get("chat_name") or editing.get("target_ref") or "")
    stored_agent = str(editing.get("agent_id") or "")
    if stored_agent and agent_id and agent_id != stored_agent:
        raise ValueError("editing cannot reassign an existing Agent connection")
    agent_id = agent_id or stored_agent or None
    if not agent_id:
        agents = registered_agent_ids_for_goal(goal)
        if len(agents) == 1:
            agent_id = next(iter(agents))
    raw_routing = editing.get("routing")
    old_routing = raw_routing if isinstance(raw_routing, Mapping) else {}
    stored_scope = str(
        old_routing.get("capture_scope")
        or (
            "configured_chat_all"
            if old_routing.get("incoming_mode") == "all"
            else "addressed_only"
        )
    )
    if old_routing.get("ingress_mode", "direct_session") == "direct_session":
        if capture_scope and capture_scope != stored_scope:
            raise ValueError(
                "upgrading a legacy connection must preserve its capture scope"
            )
        capture_scope = stored_scope
    for sibling in bindings_for_goal(read_goal_channel_binding(binding_path), goal_id):
        if (
            sibling.get("connection_id") != connection_id
            and agent_id
            and sibling.get("agent_id") == agent_id
            and sibling.get("enabled")
        ):
            raise ValueError(
                "the Agent already has another enabled connection for this Goal"
            )
    return ExistingGoalTopic(
        editing, app_ref, chat_id, chat_name, agent_id, capture_scope
    )


def resolve_conversation_policy(
    *,
    editing: Mapping[str, Any] | None,
    conversation_kind: str | None,
    executor_endpoint_id: str | None,
    ingress_mode: str | None,
    runtime_root: str | Path | None = None,
) -> tuple[str, str | None, str | None, str | None]:
    """Resolve one connection's purpose, executor observation and ingress mode.

    The machine owns its manager channel's executor. A manager connection
    therefore records the machine's current resolution and may restate it, but
    it may not override it: a caller asking for a different endpoint is refused
    with the owner named, instead of being written into a connection record
    that the route would then ignore.
    """

    from ...chat_manager import manager_connection_executor_endpoint
    from .goal_channel_transport import SAFE_PROFILE_PATTERN

    prior = (editing or {}).get("routing") or {}
    kind = conversation_kind or prior.get("conversation_kind") or "goal"
    if kind not in {"goal", "manager"}:
        raise ValueError("conversation_kind must be goal or manager")
    if kind == "manager":
        endpoint, endpoint_source = manager_connection_executor_endpoint(
            runtime_root
        )
        requested = str(executor_endpoint_id or "").strip()
        if requested and requested != endpoint:
            raise ValueError(
                "the machine steward executor setting owns this connection's "
                f"endpoint; change it there instead of requesting {requested}"
            )
        if not SAFE_PROFILE_PATTERN.fullmatch(endpoint):
            raise ValueError("executor_endpoint_id must be a safe endpoint reference")
        if ingress_mode and ingress_mode != "session_queue":
            raise ValueError(
                "the machine manager uses synchronous session_queue delivery"
            )
        return kind, endpoint, endpoint_source, "session_queue"
    return kind, None, None, ingress_mode


def _unregister_async_inbox(
    *, removed: Mapping[str, Any] | None, registry_path: Path | None, goal_id: str
) -> tuple[dict[str, Any] | None, str]:
    routing = removed.get("routing") if isinstance(removed, Mapping) else None
    routing = routing if isinstance(routing, Mapping) else {}
    agent_id = str(removed.get("agent_id") or "").strip() if removed else ""
    if routing.get("ingress_mode") != "async_inbox" or not agent_id:
        return None, agent_id
    if registry_path is None:
        error = "source registry path is required to unregister the Agent inbox"
        return {"ok": False, "error": error}, agent_id
    try:
        return configure_goal_with_global_sync(
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root_override=None,
            execute=True,
            lark_event_inbox_agent_id=agent_id,
            clear_lark_event_inbox_config=True,
        ), agent_id
    except (OSError, ValueError, TimeoutError) as exc:
        # The binding removal already landed; report the cleanup failure as a
        # failed packet instead of raising past the caller mid-disconnect.
        return {"ok": False, "error": str(exc)}, agent_id


class GoalTopicUpgradeError(OSError):
    def __init__(self, *, restored: bool) -> None:
        self.restored = restored
        super().__init__("manager route upgrade failed")

    def operation_packet(self, *, goal_id: str) -> dict[str, Any]:
        """Expose the upgrade recovery result through the existing API contract."""
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="connect_topic",
            execute=True,
            status="blocked" if self.restored else "upgrade_recovery_required",
            blocker="agent_inbox_registration_failed",
            public_summary=(
                "the manager upgrade failed; prior connection and inbox restored"
                if self.restored
                else "the manager upgrade and recovery failed; repair the existing route before retrying"
            ),
            details={"prior_route_restored": self.restored},
        )


def save_retiring_async_inbox(
    *,
    previous: Mapping[str, Any],
    registry_path: Path | None,
    binding_path: Path,
    goal_id: str,
    save: Callable[[], str],
) -> str:
    """Compensate failed upgrades under the caller's binding and source locks.

    Restore the exact source authority, not a reconstructed inbox registration.
    The shared registry reducer restores only this Goal, retaining peer writes.
    A failed compensation is reported explicitly, never as a preserved route.
    """
    if (previous.get("routing") or {}).get("ingress_mode") != "async_inbox":
        return save()
    agent_id = str(previous.get("agent_id") or "").strip()
    if not agent_id:
        raise ValueError("the prior async inbox must identify its exact Agent")
    if registry_path is None:
        raise ValueError("source registry path is required to retire the Agent inbox")
    with project_registry_transaction(
        registry_path,
        operation="upgrade_lark_manager_route",
    ) as registry_transaction:
        source_before = registry_transaction.payload_copy()
        binding_before = read_goal_channel_binding(binding_path)
        target = resolve_configure_goal_sync_target(
            registry_path=registry_path, goal_id=goal_id, runtime_root_override=None
        )
        global_path = Path(target["target_global_registry"])
        shared_source = global_path.resolve() == registry_path.resolve()
        global_before_payload = (
            source_before if shared_source else read_json(global_path)
        )
        global_goal_before = next(
            (g for g in registry_goals(global_before_payload) if g["id"] == goal_id),
            None,
        )
        try:
            # The source lock spans cleanup, binding commit, and compensation.
            cleanup = _configure_goal_with_global_sync_unlocked(
                registry_path=registry_path,
                goal_id=goal_id,
                runtime_root_override=None,
                execute=True,
                registry_transaction=registry_transaction,
                lark_event_inbox_agent_id=agent_id,
                clear_lark_event_inbox_config=True,
            )
            if not cleanup.get("ok"):
                raise OSError("prior inbox retirement did not verify")
            return save()
        except (OSError, ValueError, TimeoutError) as exc:
            restored = True

            def restore_binding() -> None:
                if read_goal_channel_binding(binding_path) != binding_before:
                    write_goal_channel_binding(binding_path, binding_before)

            def restore_source() -> None:
                if load_registry(registry_path) != source_before:
                    registry_transaction.restore()

            def restore_global() -> None:
                if shared_source:
                    return

                def reduce(current: dict[str, Any]) -> GlobalRegistryReduction:
                    goals = list(current.get("goals") or [])
                    index = next(
                        (i for i, g in enumerate(goals)
                         if isinstance(g, dict) and g.get("id") == goal_id),
                        None,
                    )
                    if global_goal_before is None:
                        if index is not None:
                            goals.pop(index)
                    elif index is None:
                        goals.append(global_goal_before)
                    else:
                        goals[index] = global_goal_before
                    return GlobalRegistryReduction({**current, "goals": goals}, {})

                mutate_global_registry(global_path, "restore_lark_manager_route", reduce)

            for restore in (restore_binding, restore_source, restore_global):
                try:
                    restore()
                except (OSError, ValueError, TimeoutError):
                    restored = False
            try:
                global_after_payload = (
                    load_registry(registry_path)
                    if shared_source
                    else read_json(global_path)
                )
                global_goal_after = next(
                    (
                        g
                        for g in registry_goals(global_after_payload)
                        if g["id"] == goal_id
                    ),
                    None,
                )
                restored = bool(
                    restored
                    and read_goal_channel_binding(binding_path) == binding_before
                    and load_registry(registry_path) == source_before
                    and global_goal_after == global_goal_before
                )
            except (OSError, ValueError, TimeoutError):
                restored = False
            raise GoalTopicUpgradeError(restored=restored) from exc
