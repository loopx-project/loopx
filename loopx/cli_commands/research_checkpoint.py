"""Explicit research capture and current-owner adoption, alongside backup IO."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def register_research_checkpoint(subparsers, add_subcommand_format) -> None:
    parser = subparsers.add_parser("research-checkpoint",
        help="Capture or explicitly adopt private research history through current owners.")
    add_subcommand_format(parser)
    # Local CLI operations compose existing artifact/session/continuation APIs;
    # they are not a new persisted lifecycle or authority vocabulary.
    parser.add_argument("operation", choices=("capture", "verify", "admit-session", "adopt-context"))
    parser.add_argument("--goal-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--project", default=".")
    parser.add_argument("--request-file", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--turn-instance-id")
    parser.add_argument("--config-path", type=Path)
    from ..control_plane.turn_driver.codex_cli import CODEX_CLI_SANDBOXES
    parser.add_argument("--sandbox", choices=CODEX_CLI_SANDBOXES)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    for name in ("source-todo-id", "todo-id", "operation-id", "expected-revision", "lease-key"):
        parser.add_argument("--" + name)
    parser.add_argument("--lease-version", type=int)
    parser.add_argument("--execute", action="store_true",
        help="Explicitly perform capture, session admission or context adoption; otherwise preview only.")


def handle_research_checkpoint(args: argparse.Namespace, *, registry_path: Path,
                               print_payload, output_format) -> int:
    from ..control_plane.effect_runtime import effect_runtime_result
    from ..history import load_registry
    from ..paths import resolve_runtime_root

    def required(*names):
        if any(getattr(args, name) is None or getattr(args, name) == "" for name in names):
            raise ValueError("Missing explicit " + ", ".join("--" + name.replace("_", "-") for name in names))
    try:
        if args.operation == "capture":
            required("request_file", "turn_instance_id", "goal_id", "agent_id", "sandbox")
        else:
            required("root", "expected_sha256")
        if args.operation == "admit-session":
            required("goal_id", "agent_id", "config_path", "sandbox")
        if args.operation == "adopt-context":
            required("goal_id", "agent_id", "sandbox", "source_todo_id", "todo_id",
                     "operation_id", "expected_revision", "lease_key", "lease_version")
        if args.operation == "verify":
            result = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "verify", "root": str(args.root), "expected_sha256": args.expected_sha256})
        elif not args.execute:
            result = {"dry_run": True, "operation": args.operation, "execution_authority_granted": False,
                "next_action": "Review the explicit source/destination and use --execute for this operation."}
        else:
            root = Path(args.runtime_root).expanduser() if args.runtime_root else resolve_runtime_root(load_registry(registry_path))
            env = {**os.environ, "LOOPX_REGISTRY": str(registry_path), "LOOPX_RUNTIME_ROOT": str(root),
                "LOOPX_PROJECT": str(Path(args.project).resolve()), "LOOPX_GOAL_ID": args.goal_id,
                "LOOPX_AGENT_ID": args.agent_id, "LOOPX_CODEX_SANDBOX": args.sandbox}
            missing = [key for key in ("CODEX_BIN", "CODEX_HOME", "MODEL_NAME", "REASONING_EFFORT") if not env.get(key)]
            if missing:
                raise ValueError("Explicit Codex Host environment is missing: " + ", ".join(missing))
            from ..control_plane.runtime.research_checkpoint import capture_research_checkpoint, adopt_todo_context
            if args.operation == "capture":
                result = capture_research_checkpoint(env, json.loads(args.request_file.read_bytes()),
                    turn_instance_id=args.turn_instance_id, timeout=args.timeout_seconds)
            elif args.operation == "admit-session":
                from ..control_plane.turn_driver.codex_checkpoint import admit_native_checkpoint_session
                result = admit_native_checkpoint_session(env, args.root, args.expected_sha256,
                    args.config_path, sandbox=args.sandbox, timeout=args.timeout_seconds)
            else:
                result = adopt_todo_context(env, args.root, args.expected_sha256,
                    source_todo_id=args.source_todo_id, todo_id=args.todo_id, operation_id=args.operation_id,
                    expected_revision=args.expected_revision, lease_key=args.lease_key, lease_version=args.lease_version)
        payload = {"ok": True, **result}
    except (ValueError, OSError, RuntimeError) as error:
        payload = {"ok": False, "error": str(error), "execution_authority_granted": False,
            "next_action": "Reconcile the named source/current-owner hold; preserve original operation identities."}
    print_payload(payload, output_format(args), lambda value: json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1
