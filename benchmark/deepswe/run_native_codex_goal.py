#!/usr/bin/env python3
"""Run the same native Codex Goal transaction used by benchmark adapters."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.benchmark_toolkit.native_codex_goal import (  # noqa: E402
    NativeGoalConfig,
    StdioNativeGoalTransport,
    compact_native_goal_receipt,
    probe_native_goal_process,
    run_native_goal_process_until_terminal,
    run_retained_decision_turn,
)
from loopx.capabilities.benchmark_toolkit.public_trajectory import (  # noqa: E402
    build_native_goal_public_trajectory_summary,
)
from loopx.capabilities.benchmark_toolkit.native_codex_isolation import (  # noqa: E402
    NativeCodexIsolationEnvelope,
    NativeCodexLoopXStateRebase,
    build_native_codex_isolation_envelope,
    rebase_native_codex_loopx_workspace_state,
)

_APP_SERVER_ARGS = ("app-server", "--listen", "stdio://", "--enable", "goals")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to a real `codex app-server`, attach an active Goal, and "
            "optionally run through every Goal continuation until terminal."
        )
    )
    parser.add_argument("--cwd", required=True, help="Task-visible working directory")
    parser.add_argument(
        "--objective-file",
        required=True,
        help="UTF-8 file containing the native Goal objective",
    )
    parser.add_argument(
        "--task-file",
        required=True,
        help="UTF-8 file containing the task instruction",
    )
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--token-budget", type=int)
    parser.add_argument("--response-timeout-seconds", type=float, default=30)
    parser.add_argument("--goal-timeout-seconds", type=float, default=21_600)
    parser.add_argument("--decision-capture", type=Path, help="Original private decision.json for one ephemeral Turn")
    parser.add_argument("--decision-sha256", help="Exact SHA-256 of --decision-capture")
    parser.add_argument(
        "--isolate",
        action="store_true",
        help="Run app-server in the benchmark-toolkit native isolation envelope",
    )
    parser.add_argument(
        "--isolation-work-dir",
        help="Runner-created per-run directory shared with the isolated worker",
    )
    parser.add_argument(
        "--private-root",
        help="Controller-private root that must stay absent inside isolation",
    )
    parser.add_argument(
        "--profile-root",
        help="Optional per-run formal LoopX profile exposed inside isolation",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Prove initialize/thread/Goal attachment without starting a model turn",
    )
    return parser


def _rebase_count(value: NativeCodexLoopXStateRebase | None) -> int:
    return value.replacement_count if value is not None else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if (args.decision_capture is None) != (args.decision_sha256 is None):
        parser.error("--decision-capture and --decision-sha256 must be supplied together")
    if args.decision_capture is not None and (args.preflight_only or args.token_budget is not None):
        parser.error("the retained decision mode runs one read-only Turn without a native Goal budget")
    isolation_paths = (
        args.isolation_work_dir,
        args.private_root,
        args.profile_root,
    )
    if args.isolate and not all(isolation_paths[:2]):
        parser.error("--isolate requires --isolation-work-dir and --private-root")
    if not args.isolate and any(isolation_paths):
        parser.error("isolation path arguments require --isolate")

    workspace_source = Path(args.cwd)
    envelope: NativeCodexIsolationEnvelope | None = None
    recovered: NativeCodexLoopXStateRebase | None = None
    rebased: NativeCodexLoopXStateRebase | None = None
    restored: NativeCodexLoopXStateRebase | None = None
    restore_required = False
    process_command: Sequence[str] | None = None
    process_cwd: str | None = None
    runtime_cwd = args.cwd
    if args.isolate:
        envelope = build_native_codex_isolation_envelope(
            executable=args.codex_bin,
            process_args=_APP_SERVER_ARGS,
            work_dir=Path(args.isolation_work_dir),
            private_root=Path(args.private_root),
            workspace_source=workspace_source,
            profile_root=Path(args.profile_root) if args.profile_root else None,
        )
        if envelope.workspace_alias is None:
            raise RuntimeError("native isolation did not create a workspace alias")
        runtime_cwd = str(envelope.workspace_alias)
        process_command = envelope.process_command
        process_cwd = str(envelope.work_dir)

    try:
        if envelope is not None and envelope.workspace_alias is not None:
            # A prior SIGKILL may have prevented the normal finally block.
            # Recover the deterministic alias before preparing this run.
            recovered = rebase_native_codex_loopx_workspace_state(
                workspace_source,
                source_root=envelope.workspace_alias,
                target_root=workspace_source,
            )
            rebased = rebase_native_codex_loopx_workspace_state(
                workspace_source,
                source_root=workspace_source,
                target_root=envelope.workspace_alias,
            )
            restore_required = True
        config = NativeGoalConfig(
            cwd=runtime_cwd,
            objective=Path(args.objective_file).read_text(encoding="utf-8").strip(),
            task_instruction=Path(args.task_file).read_text(encoding="utf-8").strip(),
            model=args.model,
            effort=args.effort,
            token_budget=args.token_budget,
        )
        if args.decision_capture is not None:
            with StdioNativeGoalTransport.spawn(
                process_command or [args.codex_bin, "app-server", "--listen", "stdio://"],
                cwd=process_cwd or runtime_cwd, response_timeout_sec=args.response_timeout_seconds,
            ) as transport:
                turn = run_retained_decision_turn(transport, config, capture=args.decision_capture,
                    digest=args.decision_sha256, timeout_sec=args.goal_timeout_seconds)
            mode = "retained_decision_turn"
        elif args.preflight_only:
            turn = probe_native_goal_process(
                config,
                codex_bin=args.codex_bin,
                process_command=process_command,
                process_cwd=process_cwd,
                response_timeout_sec=args.response_timeout_seconds,
            )
            mode = "goal_attachment_preflight"
        else:
            turn = run_native_goal_process_until_terminal(
                config,
                codex_bin=args.codex_bin,
                process_command=process_command,
                process_cwd=process_cwd,
                response_timeout_sec=args.response_timeout_seconds,
                goal_timeout_sec=args.goal_timeout_seconds,
            )
            mode = "goal_until_terminal"
    finally:
        if restore_required and envelope is not None:
            assert envelope.workspace_alias is not None
            restored = rebase_native_codex_loopx_workspace_state(
                workspace_source,
                source_root=envelope.workspace_alias,
                target_root=workspace_source,
            )
    receipt = compact_native_goal_receipt(turn)
    receipt["execution_mode"] = mode
    if args.decision_capture is not None:
        receipt["decision_capture_sha256"] = args.decision_sha256
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return int(turn.turn_status != "completed")
    receipt["public_trajectory_summary"] = (
        build_native_goal_public_trajectory_summary(
            receipt,
            adapter_id="deepswe-native-codex-goal",
        )
    )
    receipt["native_isolation"] = {
        "enabled": envelope is not None,
        "workspace_alias_used": bool(envelope and envelope.workspace_alias),
        "profile_bound": bool(envelope and envelope.profile_root),
        "control_state_found": bool(rebased and rebased.control_state_found),
        "recovered_replacement_count": _rebase_count(recovered),
        "prelaunch_replacement_count": _rebase_count(rebased),
        "restored_replacement_count": _rebase_count(restored),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
