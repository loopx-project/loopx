from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..chat.endpoints import AgentEndpointRegistry
from ..history import load_registry
from ..paths import resolve_runtime_root


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
AddFormat = Callable[[argparse.ArgumentParser], None]


def register_chat_endpoint_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "chat-endpoint",
        help="Manage owner-local ACP Agent bindings used by LoopX Chat.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "action", choices=("list", "add", "remove", "inspect-steward")
    )
    parser.add_argument(
        "--config", help="Private JSON endpoint definition used by the add action."
    )
    parser.add_argument("--agent-id", help="Custom Agent id used by the remove action.")


def handle_chat_endpoint_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int:
    try:
        registry = load_registry(registry_path) if registry_path.exists() else {}
        runtime_root = resolve_runtime_root(
            registry,
            args.runtime_root,
            registry_path=registry_path,
        )
        endpoints = AgentEndpointRegistry(runtime_root / "chat")
        if args.action == "add":
            if not args.config:
                raise ValueError("--config is required for chat-endpoint add")
            definition = json.loads(
                Path(args.config).expanduser().resolve().read_text(encoding="utf-8")
            )
            if not isinstance(definition, dict):
                raise ValueError("Agent endpoint config must be a JSON object")
            endpoint = endpoints.upsert(definition)
            payload: dict[str, object] = {
                "ok": True,
                "schema_version": "loopx_chat_endpoint_binding_v1",
                "action": "added",
                "endpoint": endpoint.public_summary(),
            }
        elif args.action == "remove":
            if not args.agent_id:
                raise ValueError("--agent-id is required for chat-endpoint remove")
            deleted = endpoints.delete(args.agent_id)
            payload = {
                "ok": deleted,
                "schema_version": "loopx_chat_endpoint_binding_v1",
                "action": "removed" if deleted else "not_found",
                "agent_id": args.agent_id,
            }
        elif args.action == "inspect-steward":
            from ..capabilities.steward_executor import (
                load_effective_steward_executor_defaults,
            )
            from ..chat_manager import manager_channel_binding, manager_channel_session
            from ..chat_store import ChatSessionStore
            from ..control_plane.operator_provider import (
                operator_credential_source,
                operator_provider_environ,
            )

            store = ChatSessionStore(runtime_root)
            payload = {
                "ok": True,
                "schema_version": "loopx_steward_executor_readback_v0",
                "binding": manager_channel_binding(
                    operator_provider_environ(runtime_root),
                    machine_defaults=load_effective_steward_executor_defaults(
                        runtime_root
                    ),
                    session=manager_channel_session(store),
                    credential_source=operator_credential_source(runtime_root),
                ),
            }
        else:
            payload = {
                "ok": True,
                "schema_version": "loopx_chat_endpoint_list_v1",
                "endpoints": [
                    endpoint.public_summary() for endpoint in endpoints.list()
                ],
            }
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_chat_endpoint_binding_v1",
            "error": str(exc),
        }
    print_payload(
        payload,
        args.format,
        lambda item: json.dumps(item, ensure_ascii=False, indent=2),
    )
    return 0 if payload.get("ok") else 1
