from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..configuration_backup import capture_configuration_backup, restore_configuration_backup, verify_configuration_backup
from ..history import load_registry
from ..paths import resolve_runtime_root


def register_configuration_backup(subparsers, add_format):
    parser = subparsers.add_parser("configuration-backup", help="Back up machine and source-owned Goal configuration; restore into an isolated checkpoint.")
    commands = parser.add_subparsers(dest="configuration_backup_command", required=True)
    export = commands.add_parser("export")
    add_format(export)
    export.add_argument("--goal-id", action="append", help="Select Goal ids; omit to capture all Goals in the invoked registry.")
    export.add_argument("--output", required=True)
    export.add_argument("--execute", action="store_true")
    for name in ("verify", "restore"):
        command = commands.add_parser(name)
        add_format(command)
        command.add_argument("--input", required=True)
        if name == "restore":
            command.add_argument("--destination", required=True)
            command.add_argument("--expected-sha256", required=True)
            command.add_argument("--execute", action="store_true")


def render_configuration_backup(payload):
    return "\n".join(["# Configuration Backup", ""] + [f"- {key}: `{payload[key]}`" for key in
        ("ok", "status", "sha256", "goal_count", "machine_configuration_present", "written", "readback_verified", "activation_performed", "error") if key in payload]) + "\n"


def handle_configuration_backup(args, *, registry_path, print_payload, output_format):
    try:
        if args.configuration_backup_command == "export":
            runtime = resolve_runtime_root(load_registry(registry_path) if registry_path.exists() else {}, args.runtime_root, registry_path=registry_path)
            backup = capture_configuration_backup(registry_path=registry_path, runtime_root=runtime, goal_ids=args.goal_id)
            payload = {**verify_configuration_backup(backup), "status": "preview", "written": False}
            if args.execute:
                path = Path(args.output).expanduser()
                # Keep backups immutable, including dangling symlink destinations.
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                    staging = Path(stream.name)
                    try:
                        stream.write(json.dumps(backup, ensure_ascii=False, indent=2) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                        # An exclusive hard-link publication cannot replace a
                        # destination created by another exporter.
                        os.link(staging, path)
                    finally:
                        staging.unlink(missing_ok=True)
                if json.loads(path.read_text()) != backup:
                    raise RuntimeError("configuration backup export readback mismatch")
                payload.update(status="exported", written=True)
        else:
            backup = json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
            payload = (verify_configuration_backup(backup) if args.configuration_backup_command == "verify" else
                restore_configuration_backup(backup, destination=Path(args.destination).expanduser(), expected_sha256=args.expected_sha256, execute=args.execute))
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "activation_performed": False}
    print_payload(payload, output_format(args), render_configuration_backup)
    return 0 if payload.get("ok") else 1
