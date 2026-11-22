from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import resolve_runtime_root
from .builtins import build_builtin_machine_configuration_registry
from .contract import (
    merge_machine_configuration_namespace,
    remove_machine_configuration_namespace,
)
from .store import (
    configure_machine_configuration,
    inspect_machine_configuration,
    read_machine_configuration,
    read_stored_machine_configuration,
    rollback_machine_configuration,
)


def _load_json_object(path_text: str) -> dict[str, Any]:
    if path_text == "-":
        payload = json.loads(sys.stdin.read())
    else:
        payload = json.loads(Path(path_text).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path_text} must contain a JSON object")
    return payload


def _render(payload: dict[str, object]) -> str:
    lines = ["# Machine Configuration", ""]
    for key in (
        "status",
        "action",
        "revision",
        "current_revision",
        "desired_revision",
        "plan_revision",
        "transaction_id",
        "rollback_id",
        "error",
    ):
        if key in payload:
            lines.append(f"- {key}: `{payload.get(key)}`")
    namespaces = payload.get("changed_namespaces")
    if isinstance(namespaces, list):
        lines.append(
            f"- changed_namespaces: `{', '.join(map(str, namespaces)) or 'none'}`"
        )
    api_key = payload.get("provider_key")
    if isinstance(api_key, dict):
        # The key itself is never printed: the fingerprint is what an operator
        # compares, and the source is what tells them where to change it.
        lines.append(
            "- api_key: `configured={configured} source={source} fingerprint={fingerprint}`".format(
                configured=api_key.get("configured"),
                source=api_key.get("source"),
                fingerprint=api_key.get("fingerprint") or "none",
            )
        )
    base_url = payload.get("base_url")
    if isinstance(base_url, dict):
        lines.append(
            "- base_url: `value={value} source={source}`".format(
                value=base_url.get("value") or "none",
                source=base_url.get("source"),
            )
        )
    if payload.get("repair"):
        lines.append(f"- repair: {payload.get('repair')}")
    catalog_namespaces = payload.get("namespaces")
    if isinstance(catalog_namespaces, list):
        lines.extend(["", "## Registered Namespaces", ""])
        for item in catalog_namespaces:
            if not isinstance(item, dict):
                continue
            namespace = str(item.get("namespace") or "unknown")
            title = str(item.get("title") or namespace)
            versions = item.get("schema_versions")
            version_text = (
                ", ".join(map(str, versions))
                if isinstance(versions, list)
                else "unknown"
            )
            lines.append(f"- `{namespace}` — {title} (`{version_text}`)")
    return "\n".join(lines) + "\n"


def register_machine_configuration_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "machine-config",
        help=(
            "Discover, inspect, preview, apply, or roll back typed machine "
            "configuration."
        ),
    )
    commands = parser.add_subparsers(dest="machine_config_command", required=True)
    describe = commands.add_parser(
        "describe", help="List registered machine-configuration namespaces."
    )
    add_subcommand_format(describe)
    preview = commands.add_parser(
        "preview",
        help="Preview an exact change and return the plan revision required by apply.",
    )
    add_subcommand_format(preview)
    preview.add_argument("--config-json", required=True)
    preview.add_argument(
        "--namespace",
        help=(
            "Treat --config-json as one namespace value and preserve every sibling "
            "namespace. Without this flag the file must contain the whole envelope."
        ),
    )
    apply = commands.add_parser(
        "apply",
        help="Apply an exact preview using its returned plan revision.",
    )
    add_subcommand_format(apply)
    apply.add_argument("--config-json", required=True)
    apply.add_argument(
        "--namespace",
        help=(
            "Treat --config-json as one namespace value and preserve every sibling "
            "namespace. Without this flag the file must contain the whole envelope."
        ),
    )
    apply.add_argument(
        "--expected-plan-revision",
        required=True,
        help="Exact plan_revision returned by machine-config preview.",
    )
    apply.add_argument("--execute", action="store_true", required=True)
    inspect = commands.add_parser("inspect")
    add_subcommand_format(inspect)
    remove = commands.add_parser(
        "remove", help="Preview or remove one machine-configuration namespace."
    )
    add_subcommand_format(remove)
    remove.add_argument("--namespace", required=True)
    remove.add_argument(
        "--expected-plan-revision",
        help="Exact plan_revision returned by the preceding removal preview.",
    )
    remove.add_argument("--execute", action="store_true")
    rollback = commands.add_parser(
        "rollback",
        help="Preview a rollback, then apply it with the returned plan revision.",
    )
    add_subcommand_format(rollback)
    rollback.add_argument("--transaction-id", required=True)
    rollback.add_argument(
        "--expected-plan-revision",
        help="Exact plan_revision returned by the preceding rollback preview.",
    )
    rollback.add_argument("--execute", action="store_true")
    credential = commands.add_parser(
        "credential",
        help=(
            "Read or write the operator model credential this machine's "
            "steward channel and managed host authenticate with."
        ),
    )
    credential_commands = credential.add_subparsers(
        dest="machine_credential_command", required=True
    )
    credential_status = credential_commands.add_parser(
        "status",
        help="Print the redacted credential status. The key is never read back.",
    )
    add_subcommand_format(credential_status)
    credential_set = credential_commands.add_parser(
        "set",
        help=(
            "Store, update, or clear the credential. Pass the value on stdin "
            "with `--config-json -` so it stays out of shell history and argv."
        ),
    )
    add_subcommand_format(credential_set)
    credential_set.add_argument(
        "--config-json",
        help=(
            "A JSON object with provider_key and/or base_url; `-` reads stdin. "
            "Omitted entirely clears nothing and changes nothing."
        ),
    )
    credential_set.add_argument(
        "--clear-api-key",
        action="store_true",
        help="Remove the stored key so the service environment applies again.",
    )
    credential_set.add_argument(
        "--clear-base-url",
        action="store_true",
        help="Remove the stored base URL so the service environment applies again.",
    )
    credential_clear = credential_commands.add_parser(
        "clear", help="Remove every stored credential field for this machine."
    )
    add_subcommand_format(credential_clear)


def handle_machine_configuration_command(
    args: argparse.Namespace,
    *,
    runtime_root_arg: str | None,
    registry_path: Path,
    output_format: Callable[..., str],
    print_payload: Callable[
        [dict[str, object], str, Callable[[dict[str, object]], str]], None
    ],
) -> int | None:
    if args.command != "machine-config":
        return None
    registry = build_builtin_machine_configuration_registry()
    runtime_root = resolve_runtime_root(
        load_registry(registry_path),
        runtime_root_arg,
        registry_path=registry_path,
    )
    try:
        if args.machine_config_command == "credential":
            from ...control_plane.operator_provider import (
                clear_operator_provider,
                operator_provider_projection,
                write_operator_provider,
            )

            if args.machine_credential_command == "status":
                payload = {
                    "ok": True,
                    **operator_provider_projection(runtime_root),
                }
            elif args.machine_credential_command == "clear":
                payload = {
                    "ok": True,
                    "action": "cleared",
                    **clear_operator_provider(runtime_root),
                }
            else:
                configuration = (
                    _load_json_object(args.config_json)
                    if args.config_json
                    else {}
                )
                payload = {
                    "ok": True,
                    "action": "stored",
                    **write_operator_provider(
                        runtime_root=runtime_root,
                        api_key=configuration.get("provider_key"),
                        base_url=configuration.get("base_url"),
                        clear_api_key=bool(args.clear_api_key),
                        clear_base_url=bool(args.clear_base_url),
                    ),
                }
        elif args.machine_config_command == "describe":
            payload = {
                "ok": True,
                **registry.public_catalog(),
            }
        elif args.machine_config_command == "preview":
            configuration = _load_json_object(args.config_json)
            if args.namespace:
                configuration = merge_machine_configuration_namespace(
                    read_stored_machine_configuration(runtime_root),
                    namespace=args.namespace,
                    namespace_configuration=configuration,
                    registry=registry,
                )
            payload = configure_machine_configuration(
                runtime_root=runtime_root,
                configuration=configuration,
                registry=registry,
            )
        elif args.machine_config_command == "apply":
            configuration = _load_json_object(args.config_json)
            if args.namespace:
                configuration = merge_machine_configuration_namespace(
                    read_stored_machine_configuration(runtime_root),
                    namespace=args.namespace,
                    namespace_configuration=configuration,
                    registry=registry,
                )
            payload = configure_machine_configuration(
                runtime_root=runtime_root,
                configuration=configuration,
                registry=registry,
                execute=args.execute,
                expected_plan_revision=args.expected_plan_revision,
            )
        elif args.machine_config_command == "inspect":
            payload = inspect_machine_configuration(runtime_root, registry=registry)
        elif args.machine_config_command == "remove":
            payload = configure_machine_configuration(
                runtime_root=runtime_root,
                configuration=remove_machine_configuration_namespace(
                    read_machine_configuration(runtime_root, registry=registry),
                    namespace=args.namespace,
                    registry=registry,
                ),
                registry=registry,
                execute=args.execute,
                expected_plan_revision=args.expected_plan_revision,
            )
        else:
            payload = rollback_machine_configuration(
                runtime_root=runtime_root,
                transaction_id=args.transaction_id,
                registry=registry,
                execute=args.execute,
                expected_plan_revision=args.expected_plan_revision,
            )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        payload = {
            "ok": False,
            "schema_version": "machine_configuration_error_v0",
            "status": "invalid_request",
            "error": str(exc),
        }
        print_payload(payload, output_format(args), _render)
        return 2
    print_payload(payload, output_format(args), _render)
    return 0


__all__ = [
    "handle_machine_configuration_command",
    "register_machine_configuration_commands",
]
