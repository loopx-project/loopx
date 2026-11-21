from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ..extensions.bundled import BUNDLED_EXTENSION_IDS, bundled_extension_manifest
from ..extensions.presentation import publish_extension_projection
from ..extensions.runtime import (
    MAX_EXTENSION_REQUEST_BYTES,
    default_extension_state_file,
    disable_extension,
    doctor_enabled_extensions,
    doctor_installed_extension,
    enable_extension,
    extension_status,
    install_extension,
    rollback_extension,
    run_standalone_extension,
)
from ..extensions.scaffold import scaffold_extension
from ..paths import effective_runtime_root

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _render(payload: dict[str, object]) -> str:
    lines = ["# LoopX Extensions", ""]
    for key in (
        "operation",
        "extension_id",
        "version",
        "revision",
        "status",
        "protocol",
        "destination",
        "module_name",
        "dry_run",
        "executed",
        "enabled",
        "changed",
        "error",
    ):
        if key in payload:
            lines.append(f"- {key}: `{payload.get(key)}`")
    extensions = payload.get("extensions")
    if isinstance(extensions, list):
        lines.append(f"- extension_count: `{len(extensions)}`")
        for item in extensions:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('id')}`: enabled=`{item.get('enabled')}`, "
                    f"doctor_verified=`{item.get('doctor_verified')}`"
                )
    return "\n".join(lines) + "\n"


def _state_file(
    args: argparse.Namespace, runtime_root_arg: str | None, *, registry_path: Path,
) -> Path:
    override = getattr(args, "extension_state_file", None)
    if override:
        return Path(override).expanduser()
    if runtime_root_arg is None:
        runtime_root_arg = str(effective_runtime_root(registry_path, None))
    return default_extension_state_file(runtime_root_arg)


def _add_common(
    parser: argparse.ArgumentParser,
    add_subcommand_format: AddFormat,
) -> None:
    add_subcommand_format(parser)
    parser.add_argument(
        "--state-file",
        dest="extension_state_file",
        help="Override the local extension activation state file.",
    )


def _add_manifest_source(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest")
    source.add_argument("--bundled", choices=BUNDLED_EXTENSION_IDS)


def register_extension_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "extension",
        help="Manage explicitly enabled subprocess extension providers.",
    )
    commands = parser.add_subparsers(dest="extension_command", required=True)

    init = commands.add_parser(
        "init",
        help="Preview or create a minimal independently packaged extension starter.",
    )
    add_subcommand_format(init)
    init.add_argument("extension_id")
    init.add_argument(
        "--destination",
        help="Target directory. Defaults to packages/<extension-id>.",
    )
    init.add_argument("--version", default="0.1.0")
    init.add_argument("--execute", action="store_true")

    list_parser = commands.add_parser("list", help="List installed extensions.")
    _add_common(list_parser, add_subcommand_format)

    for command in ("install", "upgrade"):
        operation = commands.add_parser(
            command,
            help=(
                "Register a preinstalled extension after its doctor passes."
                if command == "install"
                else "Activate a new manifest revision after its doctor passes."
            ),
        )
        _add_common(operation, add_subcommand_format)
        _add_manifest_source(operation)
        operation.add_argument("--execute", action="store_true")

    for command in ("enable", "disable", "rollback", "doctor"):
        operation = commands.add_parser(command)
        _add_common(operation, add_subcommand_format)
        if command == "doctor":
            operation.add_argument("extension_id", nargs="?")
        else:
            operation.add_argument("extension_id")
        if command != "doctor":
            operation.add_argument("--execute", action="store_true")
        else:
            operation.add_argument(
                "--all-enabled",
                action="store_true",
                help=(
                    "Revalidate every enabled extension after an install, update, "
                    "or rollback."
                ),
            )
            operation.add_argument(
                "--execute",
                action="store_true",
                help="Run the configured read-only provider probe.",
            )

    run = commands.add_parser(
        "run",
        help="Invoke one enabled standalone extension through its managed runtime.",
    )
    _add_common(run, add_subcommand_format)
    run.add_argument("extension_id")
    run.add_argument(
        "--input-json",
        required=True,
        help="Path to one provider request JSON object, or '-' for stdin.",
    )
    run.add_argument("--execute", action="store_true")

    publish_projection = commands.add_parser(
        "publish-projection",
        help="Validate and publish one declared extension presentation surface.",
    )
    _add_common(publish_projection, add_subcommand_format)
    publish_projection.add_argument("extension_id")
    publish_projection.add_argument("surface_id")
    publish_projection.add_argument(
        "--input-json",
        required=True,
        help="Path to one provider request JSON object, or '-' for stdin.",
    )
    publish_projection.add_argument("--execute", action="store_true")


def _load_json_object(path_text: str) -> dict[str, object]:
    try:
        if path_text == "-":
            binary_stdin = getattr(sys.stdin, "buffer", None)
            if binary_stdin is not None:
                raw = binary_stdin.read(MAX_EXTENSION_REQUEST_BYTES + 1)
            else:
                raw = sys.stdin.read(MAX_EXTENSION_REQUEST_BYTES + 1).encode("utf-8")
        else:
            with Path(path_text).open("rb") as input_file:
                raw = input_file.read(MAX_EXTENSION_REQUEST_BYTES + 1)
        if len(raw) > MAX_EXTENSION_REQUEST_BYTES:
            raise ValueError("extension run input exceeds the 1000000-byte limit")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("extension run input must be a readable JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("extension run input must be a JSON object")
    return payload


def handle_extension_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "extension":
        return None
    state_file = _state_file(args, runtime_root_arg, registry_path=registry_path)
    try:
        if args.extension_command == "init":
            payload = scaffold_extension(
                args.extension_id,
                destination=args.destination,
                version=args.version,
                execute=args.execute,
            )
        elif args.extension_command == "list":
            payload = extension_status(state_file=state_file)
        elif args.extension_command in {"install", "upgrade"}:
            manifest_path = (
                bundled_extension_manifest(args.bundled)
                if args.bundled
                else Path(args.manifest).expanduser()
            )
            payload = install_extension(
                manifest_path,
                state_file=state_file,
                operation=args.extension_command,
                execute=args.execute,
            )
        elif args.extension_command == "enable":
            payload = enable_extension(
                args.extension_id,
                state_file=state_file,
                execute=args.execute,
            )
        elif args.extension_command == "disable":
            payload = disable_extension(
                args.extension_id,
                state_file=state_file,
                execute=args.execute,
            )
        elif args.extension_command == "rollback":
            payload = rollback_extension(
                args.extension_id,
                state_file=state_file,
                execute=args.execute,
            )
        elif args.extension_command == "run":
            payload = run_standalone_extension(
                args.extension_id,
                state_file=state_file,
                request=_load_json_object(args.input_json),
                execute=args.execute,
            )
        elif args.extension_command == "publish-projection":
            payload = publish_extension_projection(
                args.extension_id,
                args.surface_id,
                state_file=state_file,
                request=_load_json_object(args.input_json),
                execute=args.execute,
            )
        else:
            if bool(args.extension_id) == bool(args.all_enabled):
                raise ValueError(
                    "extension doctor requires exactly one extension_id or "
                    "--all-enabled"
                )
            if args.all_enabled:
                payload = doctor_enabled_extensions(
                    state_file=state_file,
                    execute=args.execute,
                )
            else:
                payload = doctor_installed_extension(
                    args.extension_id,
                    state_file=state_file,
                    execute=args.execute,
                )
    except ValueError as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_extension_error_v0",
            "status": "invalid_request",
            "error": str(exc),
        }
        print_payload(payload, output_format(args), _render)
        return 2
    print_payload(payload, output_format(args), _render)
    return 0 if payload.get("ok", True) else 1
