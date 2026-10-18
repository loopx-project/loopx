"""Explicit research capture and current-owner adoption, alongside backup IO."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


def register_research_checkpoint(subparsers, add_subcommand_format) -> None:
    parser = subparsers.add_parser("research-checkpoint",
        help="Capture or explicitly adopt private research history through current owners.")
    add_subcommand_format(parser)
    # Local CLI operations compose existing artifact/session/continuation APIs;
    # they are not a new persisted lifecycle or authority vocabulary.
    parser.add_argument("operation", choices=("capture", "verify", "admit-session", "adopt-context", "rotate"))
    parser.add_argument("--goal-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--project", default=".")
    parser.add_argument("--request-file", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-plan-sha256", help="Rotate only the unchanged previewed retention plan")
    parser.add_argument("--watch", action="store_true", help="Explicit rotate --execute loop; stop the process to disable")
    parser.add_argument("--interval-seconds", type=float, default=300)
    parser.add_argument("--turn-instance-id")
    parser.add_argument("--config-path", type=Path)
    from ..control_plane.turn_driver.codex_cli import CODEX_CLI_SANDBOXES
    parser.add_argument("--sandbox", choices=CODEX_CLI_SANDBOXES)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    for name in ("source-todo-id", "todo-id", "operation-id", "expected-revision", "lease-key"):
        parser.add_argument("--" + name)
    parser.add_argument("--lease-version", type=int)
    parser.add_argument("--execute", action="store_true",
        help="Explicitly perform capture, adoption or retention deletion; otherwise preview only.")


def handle_research_checkpoint(args: argparse.Namespace, *, registry_path: Path,
                               print_payload, output_format) -> int:
    from ..control_plane.effect_runtime import effect_runtime_result
    from ..history import load_registry
    from ..paths import resolve_runtime_root

    def required(*names):
        if any(getattr(args, name) is None or getattr(args, name) == "" for name in names):
            raise ValueError("Missing explicit " + ", ".join("--" + name.replace("_", "-") for name in names))
    try:
        if args.watch and (args.operation != "rotate" or not args.execute or args.expected_plan_sha256):
            raise ValueError("--watch requires rotate --execute without a frozen preview digest")
        if args.expected_plan_sha256 and args.operation != "rotate":
            raise ValueError("--expected-plan-sha256 requires rotate")
        if args.operation == "rotate":
            required("root")
            if not 10 <= args.interval_seconds <= 86400:
                raise ValueError("Retention interval must be between 10 and 86400 seconds")
            from ..hosts.checkpoint_retention import bank_generation, retention_policy_bytes, rotate_bank
            previous = None
            next_due = None
            while True:
                # A supervisor bank publishes its editable default policy.
                # An unmanaged, explicitly selected bank uses the typed default
                # without creating any state during preview.
                raw = retention_policy_bytes(args.root, args.request_file)
                generation = bank_generation(args.root, raw)
                if generation != previous or (next_due is not None and time.time() >= next_due):
                    result = rotate_bank(args.root, json.loads(raw), execute=args.execute,
                                         expected_plan_sha256=args.expected_plan_sha256)
                    payload = {"ok": True, **result}
                    print_payload(payload, output_format(args), lambda value: json.dumps(value, ensure_ascii=False, indent=2))
                    sys.stdout.flush()
                    # Remember the post-cleanup bank; do not wake just because
                    # this operation removed its own selected directories.
                    after = bank_generation(args.root, raw)
                    previous = after if {p[0] for p in after[1]} == {p["id"] for p in result["retained"]} else None
                    next_due = result["grace_expires_at"]
                if not args.watch:
                    return 0
                # No model/Goal/control-plane reads. Only the selected local
                # bank and explicit policy are revisited at this bounded rate.
                time.sleep(args.interval_seconds)
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
            from ..hosts.checkpoint_retention import checkpoint_bank_use
            with checkpoint_bank_use(args.root):
                result = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "verify", "root": str(args.root), "expected_sha256": args.expected_sha256}, large_local_snapshot=True)
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
            from ..hosts.research_checkpoint import capture_research_checkpoint, adopt_todo_context
            if args.operation == "capture":
                result = capture_research_checkpoint(env, json.loads(args.request_file.read_bytes()),
                    turn_instance_id=args.turn_instance_id, timeout=args.timeout_seconds)
            elif args.operation == "admit-session":
                from ..hosts.codex_checkpoint import admit_native_checkpoint_session
                from ..hosts.checkpoint_retention import checkpoint_bank_use
                with checkpoint_bank_use(args.root):
                    result = admit_native_checkpoint_session(env, args.root, args.expected_sha256,
                        args.config_path, sandbox=args.sandbox, timeout=args.timeout_seconds)
            else:
                from ..hosts.checkpoint_retention import checkpoint_bank_use
                with checkpoint_bank_use(args.root):
                    result = adopt_todo_context(env, args.root, args.expected_sha256,
                        source_todo_id=args.source_todo_id, todo_id=args.todo_id, operation_id=args.operation_id,
                        expected_revision=args.expected_revision, lease_key=args.lease_key, lease_version=args.lease_version)
        payload = {"ok": True, **result}
    except (ValueError, OSError, RuntimeError) as error:
        payload = {"ok": False, "error": str(error), "execution_authority_granted": False,
            "next_action": "Reconcile the named source/current-owner hold; preserve original operation identities."}
    print_payload(payload, output_format(args), lambda value: json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1
