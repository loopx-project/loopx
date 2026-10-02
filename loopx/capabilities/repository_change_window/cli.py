from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import sys

from ...history import load_registry
from ...paths import resolve_runtime_root, select_default_runtime_root
from .git_hook import (
    EnforcementLevel,
    git_hook_provider_status,
    hook_runtime_contract,
    install_git_hook_provider,
    run_git_hook_provider,
    uninstall_git_hook_provider,
    verify_git_hook_provider,
)
from .ledger import (
    list_pending_changes,
    reconcile_pending_changes,
    record_pending_change,
    resolve_pending_change,
    verify_pending_change,
)
from .policy import build_policy
from .repository import RepositoryChangeWindowError


AddFormat = Callable[[argparse.ArgumentParser], None]
FormatSelector = Callable[..., str]
PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]


def _repo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path("."),
        help="Git worktree whose repository policy or pending change is inspected.",
    )


def _execute_argument(parser: argparse.ArgumentParser, *, action: str) -> None:
    parser.add_argument(
        "--execute",
        action="store_true",
        help=f"Apply the {action}; preview is the default.",
    )


def register_repository_change_window_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "change-window",
        help="Gate repository changes and retain global references to blocked unmerged work.",
    )
    commands = parser.add_subparsers(dest="change_window_command", required=True)

    install = commands.add_parser(
        "install",
        help="Preview or install the repository-local Git-hook provider.",
    )
    add_subcommand_format(install)
    _repo_argument(install)
    install.add_argument("--timezone", default="Asia/Shanghai")
    install.add_argument(
        "--blocked-weekday",
        action="append",
        default=[],
        help="Blocked local weekday. Repeat; defaults to mon through fri.",
    )
    install.add_argument("--blocked-start", default="10:00")
    install.add_argument("--blocked-end", default="21:00")
    install.add_argument(
        "--enforcement-level",
        choices=tuple(item.value for item in EnforcementLevel),
        default=EnforcementLevel.HOOK_ONLY.value,
        help=(
            "Local enforcement strength. `reference_guard` also protects new commit "
            "ref updates that `git commit --no-verify` cannot skip."
        ),
    )
    install.add_argument(
        "--replace",
        action="store_true",
        help="Replace an installed policy only after provider drift and policy are reviewed.",
    )
    _execute_argument(install, action="provider installation")

    status = commands.add_parser(
        "status",
        help="Read provider installation, policy, current decision, and hook health.",
    )
    add_subcommand_format(status)
    _repo_argument(status)

    verify = commands.add_parser(
        "verify",
        help="Verify the repository provider or one globally recorded pending change.",
    )
    add_subcommand_format(verify)
    _repo_argument(verify)
    verify.add_argument("--change-id")

    uninstall = commands.add_parser(
        "uninstall",
        help="Preview or remove the provider and restore the previous hook route.",
    )
    add_subcommand_format(uninstall)
    _repo_argument(uninstall)
    _execute_argument(uninstall, action="provider removal")

    record = commands.add_parser(
        "record",
        help="Preview or register a durable public-safe reference to unmerged work.",
    )
    add_subcommand_format(record)
    _repo_argument(record)
    record.add_argument("--change-id")
    record.add_argument("--goal-id")
    record.add_argument("--todo-id")
    record.add_argument("--pr-ref")
    record.add_argument("--write-scope", action="append", default=[])
    record.add_argument("--validation-ref", action="append", default=[])
    record.add_argument("--supersedes")
    _execute_argument(record, action="pending-change ledger write")

    reconcile = commands.add_parser(
        "reconcile",
        help=(
            "Inventory dirty linked worktrees during a blocked window and repair "
            "missing global pending-change records."
        ),
    )
    add_subcommand_format(reconcile)
    _repo_argument(reconcile)
    reconcile.add_argument(
        "--all-linked-worktrees",
        action="store_true",
        help=(
            "Scan every linked worktree in the repository. The default reconciles "
            "only --repo-path so routine writeback stays bounded."
        ),
    )
    _execute_argument(reconcile, action="linked-worktree pending-change reconciliation")

    list_parser = commands.add_parser(
        "list",
        help="List unresolved or terminal pending changes from the shared runtime ledger.",
    )
    add_subcommand_format(list_parser)
    list_parser.add_argument(
        "--state", choices=("open", "resolved", "all"), default="open"
    )
    list_parser.add_argument("--repository-id")

    resolve = commands.add_parser(
        "resolve",
        help="Preview or append a typed merged, superseded, or abandoned resolution.",
    )
    add_subcommand_format(resolve)
    resolve.add_argument("--change-id", required=True)
    resolve.add_argument(
        "--resolution",
        choices=("merged", "superseded", "abandoned"),
        required=True,
    )
    resolve.add_argument("--evidence", required=True)
    resolve.add_argument("--superseded-by")
    _execute_argument(resolve, action="pending-change resolution")

    hook = commands.add_parser(
        "hook",
        help="Internal managed Git-hook entrypoint; use install/status/verify for lifecycle operations.",
    )
    add_subcommand_format(hook)
    _repo_argument(hook)
    hook.add_argument(
        "--event",
        choices=(
            "pre-commit",
            "pre-push",
            "reference-transaction",
            "ssh-transport",
        ),
        required=True,
    )
    hook.add_argument("hook_args", nargs=argparse.REMAINDER)

    hook_runtime = commands.add_parser(
        "hook-runtime",
        help="Report the typed runtime contract used by managed Git hooks.",
    )
    add_subcommand_format(hook_runtime)


def _runtime_root(registry_path: Path, runtime_root_arg: str | None) -> Path:
    if registry_path.is_file():
        registry = load_registry(registry_path)
        return Path(
            resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
        )
    return (
        Path(runtime_root_arg).expanduser()
        if runtime_root_arg
        else select_default_runtime_root()
    )


def render_repository_change_window_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Repository Change Window",
        "",
        f"- ok: `{str(payload.get('ok')).lower()}`",
        f"- status: `{payload.get('status') or payload.get('transition')}`",
    ]
    for key in (
        "repository_id",
        "change_id",
        "count",
        "dry_run",
        "changed",
        "duplicate",
    ):
        if key in payload:
            lines.append(f"- {key}: `{payload.get(key)}`")
    decision = payload.get("decision")
    if isinstance(decision, dict):
        lines.extend(
            [
                f"- allowed: `{decision.get('allowed')}`",
                f"- next_eligible_at: `{decision.get('next_eligible_at')}`",
            ]
        )
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    changes = payload.get("changes")
    if isinstance(changes, list):
        lines.extend(["", "## Pending Changes", ""])
        for item in changes:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('change_id')}` {item.get('repository_id')} "
                    f"`{item.get('branch') or item.get('checkout_id')}` "
                    f"state=`{item.get('state')}`"
                )
    return "\n".join(lines).rstrip() + "\n"


def handle_repository_change_window_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "change-window":
        return None
    runtime_root = _runtime_root(registry_path, runtime_root_arg)
    try:
        command = str(args.change_window_command)
        if command == "install":
            weekdays = args.blocked_weekday or ["mon", "tue", "wed", "thu", "fri"]
            payload = install_git_hook_provider(
                repo_path=args.repo_path,
                policy=build_policy(
                    timezone_name=args.timezone,
                    weekdays=weekdays,
                    blocked_start=args.blocked_start,
                    blocked_end=args.blocked_end,
                ),
                enforcement_level=args.enforcement_level,
                replace=bool(args.replace),
                execute=bool(args.execute),
            )
        elif command == "status":
            payload = git_hook_provider_status(repo_path=args.repo_path)
        elif command == "verify":
            payload = (
                verify_pending_change(
                    runtime_root=runtime_root,
                    change_id=args.change_id,
                )
                if args.change_id
                else verify_git_hook_provider(repo_path=args.repo_path)
            )
        elif command == "uninstall":
            payload = uninstall_git_hook_provider(
                repo_path=args.repo_path,
                execute=bool(args.execute),
            )
        elif command == "record":
            provider = git_hook_provider_status(repo_path=args.repo_path)
            decision = provider.get("decision")
            if not isinstance(decision, dict):
                observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                decision = {
                    "schema_version": "repository_change_window_decision_v0",
                    "allowed": True,
                    "reason": "manual_record_without_installed_provider",
                    "observed_at": observed,
                    "next_eligible_at": observed,
                }
            payload = record_pending_change(
                runtime_root=runtime_root,
                repo_path=args.repo_path,
                decision=decision,
                source="manual_cli",
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                change_id=args.change_id,
                pr_ref=args.pr_ref,
                write_scopes=args.write_scope,
                validation_refs=args.validation_ref,
                supersedes=args.supersedes,
                execute=bool(args.execute),
            )
        elif command == "reconcile":
            provider = git_hook_provider_status(repo_path=args.repo_path)
            decision = provider.get("decision")
            if not provider.get("installed") or not isinstance(decision, dict):
                raise RepositoryChangeWindowError(
                    "pending-change reconciliation requires an installed repository provider"
                )
            payload = reconcile_pending_changes(
                runtime_root=runtime_root,
                repo_path=args.repo_path,
                decision=decision,
                include_linked_worktrees=bool(args.all_linked_worktrees),
                execute=bool(args.execute),
            )
        elif command == "list":
            payload = list_pending_changes(
                runtime_root=runtime_root,
                state=args.state,
                repository_id=args.repository_id,
            )
        elif command == "resolve":
            payload = resolve_pending_change(
                runtime_root=runtime_root,
                change_id=args.change_id,
                resolution=args.resolution,
                evidence=args.evidence,
                superseded_by=args.superseded_by,
                execute=bool(args.execute),
            )
        elif command == "hook-runtime":
            payload = hook_runtime_contract()
        elif command == "hook":
            hook_args = list(args.hook_args)
            if hook_args[:1] == ["--"]:
                hook_args = hook_args[1:]
            payload = run_git_hook_provider(
                repo_path=args.repo_path,
                runtime_root=runtime_root,
                event=args.event,
                hook_args=hook_args,
                hook_stdin=sys.stdin.buffer.read(),
            )
        else:
            raise RepositoryChangeWindowError(
                f"unsupported change-window command `{command}`"
            )
    except (OSError, TypeError, ValueError) as exc:
        payload = {
            "ok": False,
            "schema_version": "repository_change_window_error_v0",
            "status": "error",
            "error": str(exc),
        }
    if not (args.change_window_command == "hook"
            and os.environ.get("LOOPX_GIT_HOOK_QUIET_SUCCESS") == "1"
            and payload.get("ok")):
        print_payload(
            payload, output_format(args), render_repository_change_window_markdown
        )
    if args.change_window_command == "hook" and isinstance(
        payload.get("exit_code"), int
    ):
        return int(payload["exit_code"])
    return 0 if payload.get("ok") else 1
