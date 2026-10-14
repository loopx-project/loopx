"""Resumable multi-Agent orchestration for Lark Goal Topic connections."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.todos.contract import normalize_todo_claimed_by
from .goal_channel_contracts import operation_packet
from .goal_topic_connections import connect_lark_goal_topic
from .cli_transport import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    default_subprocess_runner,
)


def connect_lark_goal_topics(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    target_path: Path,
    binding_path: Path,
    app_refs_by_agent: Mapping[str, str],
    chat_id: str,
    chat_name: str,
    incoming_mode: str = "mentions",
    session_ids_by_agent: Mapping[str, str] | None = None,
    capture_scope: str | None = None,
    ingress_mode: str = "async_inbox",
    reply_mode: str = "topic_reply",
    registry_path: Path | None = None,
    runtime_root: str | Path | None = None,
    execute: bool = True,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> dict[str, Any]:
    """Preview every Agent before running one idempotent connection at a time."""

    if not app_refs_by_agent:
        raise ValueError("app_refs_by_agent must contain at least one Agent")
    if len(app_refs_by_agent) > 16:
        raise ValueError("app_refs_by_agent supports at most 16 Agents")

    requested: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_agent_id, raw_app_ref in app_refs_by_agent.items():
        agent_id = normalize_todo_claimed_by(raw_agent_id)
        if not agent_id:
            raise ValueError("each batch item must name a public-safe Agent id")
        if agent_id in seen:
            raise ValueError("each Agent may appear only once in a batch")
        seen.add(agent_id)
        requested.append((agent_id, str(raw_app_ref or "").strip()))

    sessions = session_ids_by_agent or {}
    common = {
        "registry": registry,
        "goal_id": goal_id,
        "target_path": target_path,
        "binding_path": binding_path,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "incoming_mode": incoming_mode,
        "capture_scope": capture_scope,
        "ingress_mode": ingress_mode,
        "reply_mode": reply_mode,
        "registry_path": registry_path,
        "runtime_root": runtime_root,
        "runner": runner,
        "cli_bin": cli_bin,
    }
    previews: list[dict[str, Any]] = []
    normalized: list[tuple[str, str]] = []
    for agent_id, app_ref in requested:
        preview = connect_lark_goal_topic(
            **common,
            agent_id=agent_id,
            app_ref=app_ref,
            session_id=str(sessions.get(agent_id) or "").strip() or None,
            execute=False,
        )
        validated_app_ref = str(
            (preview.get("details") or {}).get("app_ref") or app_ref
        )
        normalized.append((agent_id, validated_app_ref))
        previews.append(
            {
                "agent_id": agent_id,
                "app_ref": validated_app_ref,
                "status": str(preview.get("status") or "blocked"),
            }
        )
        if not preview.get("ok"):
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="connect_topics",
                execute=execute,
                status="blocked",
                blocker=str(preview.get("blocker") or "batch_preflight_failed"),
                public_summary="one Agent connection did not pass batch preflight",
                details={
                    "connections": previews,
                    "failed_agent_id": agent_id,
                    "completed_agent_ids": [],
                },
            )

    if not execute:
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="connect_topics",
            execute=False,
            status="preview_ready",
            public_summary=f"previewed {len(normalized)} Agent connections",
            details={"connections": previews, "completed_agent_ids": []},
        )

    results: list[dict[str, Any]] = []
    completed: list[str] = []
    external_write_performed = False
    for agent_id, app_ref in normalized:
        result = connect_lark_goal_topic(
            **common,
            agent_id=agent_id,
            app_ref=app_ref,
            session_id=str(sessions.get(agent_id) or "").strip() or None,
            execute=True,
        )
        external_write_performed = external_write_performed or bool(
            result.get("external_write_performed")
        )
        results.append(
            {
                "agent_id": agent_id,
                "app_ref": app_ref,
                "status": str(result.get("status") or "failed"),
            }
        )
        if not result.get("ok"):
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="connect_topics",
                execute=True,
                status="partially_connected" if completed else "failed",
                blocker=str(result.get("blocker") or "provider_api_failed"),
                public_summary="the Agent connection batch can be resumed safely",
                external_write_performed=external_write_performed,
                details={
                    "connections": results,
                    "failed_agent_id": agent_id,
                    "completed_agent_ids": completed,
                },
            )
        completed.append(agent_id)

    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="connect_topics",
        execute=True,
        status="connected",
        public_summary=f"connected {len(completed)} Agents to dedicated Lark topics",
        external_write_performed=external_write_performed,
        readback_verified=True,
        details={"connections": results, "completed_agent_ids": completed},
    )


def upgrade_lark_goal_topics(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    target_path: Path,
    binding_path: Path,
    registry_path: Path,
    connection_id: str | None = None,
    agent_id: str | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> dict[str, Any]:
    """Resume old-route upgrades, preserving identities, Topics and capture scope.

    An explicit recipient requires an exact connection. Independent blocked
    routes do not prevent unambiguous routes from upgrading.
    """
    from .goal_channel_contracts import bindings_for_goal, read_goal_channel_binding

    if agent_id and not connection_id:
        raise ValueError("agent_id requires an exact connection_id for upgrade")
    connections = bindings_for_goal(read_goal_channel_binding(binding_path), goal_id)
    if connection_id:
        connections = [
            item for item in connections if item.get("connection_id") == connection_id
        ]
        if not connections:
            raise ValueError("connection_id is not registered for this Goal")
    results: list[dict[str, Any]] = []
    for connection in connections:
        if connection.get("provider") != "lark" or not connection.get("enabled"):
            continue
        ref = str(connection["connection_id"])
        if (connection.get("routing") or {}).get(
            "ingress_mode", "direct_session"
        ) != "direct_session":
            results.append(
                {"connection_id": ref, "status": "already_current", "ok": True}
            )
            continue
        try:
            result = connect_lark_goal_topic(
                registry=registry,
                registry_path=registry_path,
                goal_id=goal_id,
                target_path=target_path,
                binding_path=binding_path,
                connection_id=ref,
                agent_id=agent_id,
                execute=execute,
                runner=runner,
                cli_bin=cli_bin,
            )
            results.append(
                {
                    "connection_id": ref,
                    "ok": bool(result.get("ok")),
                    "status": result.get("status"),
                    "blocker": result.get("blocker"),
                    "details": result.get("details", {}),
                    "readback_verified": bool(result.get("readback_verified")),
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "connection_id": ref,
                    "ok": False,
                    "status": "blocked",
                    "blocker": "upgrade_context_required",
                    "public_summary": str(exc),
                }
            )
    ok = all(item["ok"] for item in results)
    return operation_packet(
        ok=ok,
        goal_id=goal_id,
        operation="upgrade_topics",
        execute=execute,
        status=("upgraded" if execute else "preview_ready")
        if ok
        else "upgrade_incomplete",
        public_summary="checked existing Lark connections for Agent inbox upgrade",
        details={"connections": results},
    )
