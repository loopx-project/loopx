from __future__ import annotations

from pathlib import Path
import json

from ..capabilities.benchmark_toolkit.trajectory_assessment import (
    execute,
    prepare,
    read_job,
    request_cancel,
)


def register_benchmark_trajectory_commands(subparsers, add_format) -> None:
    parser = subparsers.add_parser(
        "trajectory-assess",
        help="Preview or explicitly execute a source-bound trajectory assessment.",
    )
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="max")
    parser.add_argument("--service-tier", default="fast")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--retry", action="store_true")
    add_format(parser)
    register = subparsers.add_parser(
        "trajectory-pack-register",
        help="Register a fixed source-verified case pack for an owner Goal; does not run a model.",
    )
    register.add_argument("--goal-id", required=True)
    register.add_argument("--input-json", type=Path, required=True)
    register.add_argument("--source-root", type=Path, required=True)
    register.add_argument("--title", required=True)
    add_format(register)
    listing = subparsers.add_parser(
        "trajectory-packs",
        help="Discover registered case packs without starting analysis.",
    )
    listing.add_argument("--goal-id", required=True)
    add_format(listing)
    for name in ("trajectory-assessment-status", "trajectory-assessment-cancel"):
        parser = subparsers.add_parser(
            name, help="Read an analysis receipt or request cooperative cancellation."
        )
        parser.add_argument("--directory", type=Path, required=True)
        add_format(parser)


def _handle_benchmark_trajectory_command(
    args, *, registry_path, print_payload, output_format
) -> int | None:
    if args.benchmark_command not in {
        "trajectory-assess",
        "trajectory-assessment-status",
        "trajectory-assessment-cancel",
        "trajectory-pack-register",
        "trajectory-packs",
    }:
        return None
    if args.benchmark_command in {"trajectory-pack-register", "trajectory-packs"}:
        from ..history import load_registry
        from ..paths import resolve_runtime_root
        from ..capabilities.benchmark_toolkit.trajectory_assessment_store import (
            discover,
            register_pack,
        )

        root = resolve_runtime_root(
            load_registry(registry_path), args.runtime_root, registry_path=registry_path
        )
        scope = {"runtime_root": root, "owner_goal_id": args.goal_id}
        result = (
            register_pack(
                **scope,
                input_path=args.input_json,
                source_root=args.source_root,
                title=args.title,
            )
            if args.benchmark_command == "trajectory-pack-register"
            else discover(**scope)
        )
    elif args.benchmark_command == "trajectory-assess":
        if args.timeout_seconds <= 0:
            raise ValueError("--timeout-seconds must be positive")
        prepared = prepare(
            args.input_json,
            model=args.model,
            effort=args.effort,
            service_tier=args.service_tier,
        )
        result = (
            execute(
                prepared,
                args.output_dir,
                retry=args.retry,
                timeout_seconds=args.timeout_seconds,
            )
            if args.execute
            else {
                "ok": True,
                "status": "preview",
                "analysis_key": prepared["analysis_key"],
                "method_version": prepared["method_version"],
                "config": prepared["config"],
                "scope": prepared["input"]["scope"],
                "episodes": len(prepared["input"]["episodes"]),
                "directory": str(args.output_dir.resolve() / prepared["analysis_key"]),
                "execution_requires": "--execute; uses the local Codex account; analysis cost is separate from solver cost",
            }
        )
    elif args.benchmark_command == "trajectory-assessment-status":
        result = read_job(args.directory)
    else:
        result = request_cancel(args.directory)
    print_payload(
        result,
        output_format(args),
        lambda value: json.dumps(value, ensure_ascii=False, indent=2),
    )
    return (
        0 if result.get("ok") or result.get("status") in {"running", "cancelled"} else 1
    )


def handle_benchmark_trajectory_command(
    args, *, registry_path, print_payload, output_format
) -> int | None:
    try:
        return _handle_benchmark_trajectory_command(
            args,
            registry_path=registry_path,
            print_payload=print_payload,
            output_format=output_format,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
        print_payload(
            result,
            output_format(args),
            lambda value: json.dumps(value, ensure_ascii=False, indent=2),
        )
        return 1
