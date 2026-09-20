"""Registration and dispatch for the update command.

Refs GH-C06. This group was carved out of `support_control.py`, which registers
several unrelated top-level commands in one module that sits just under the
1000-line default budget in
`examples/cli-command-module-size-ownership-command-modularization-smoke.py`.

`update` is one group: it inspects, plans, applies, or rolls back the active
LoopX installation, and it is the only support-control command that mutates the
install itself. Its parser flags and its dispatch branch therefore move
together, and the public invocation is unchanged.

The command stays inside `SUPPORT_CONTROL_COMMANDS` because the top-level
support-control router dispatches on that set.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..self_update import (
    UpdateAction,
    build_rollback_plan,
    build_update_plan,
    execute_rollback_plan,
    execute_update_plan,
    render_update_plan_markdown,
    resolve_update_action,
)
from .support_control_registry import explicit_global_registry

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

UPDATE_CONTROL_COMMANDS = {"update"}


def register_update_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    update_parser = subparsers.add_parser(
        "update",
        help="Inspect or apply an update using the active installation owner.",
        description=(
            "Use `update check` for a read-only freshness probe, `update plan` for the "
            "full no-write plan, or `update apply` for an explicit archive-snapshot "
            "mutation. Bare `update` remains a read-only plan."
        ),
    )
    add_subcommand_format(update_parser)
    update_parser.add_argument(
        "update_action",
        nargs="?",
        choices=tuple(action.value for action in UpdateAction),
        help="Explicit intent: check (read only), plan (read only), or apply (mutating).",
    )
    update_mode = update_parser.add_mutually_exclusive_group()
    update_mode.add_argument(
        "--check",
        action="store_true",
        help="Compatibility alias for `loopx update check`.",
    )
    update_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Compatibility alias for `loopx update plan`.",
    )
    update_mode.add_argument(
        "--execute",
        action="store_true",
        help="Compatibility alias for `loopx update apply`; prefer the explicit action.",
    )
    update_mode.add_argument(
        "--rollback",
        metavar="RELEASE_ID",
        help="Repoint the user-local loopx command to a release id, or use `previous` for the prior snapshot.",
    )
    update_parser.add_argument(
        "--repo",
        help="GitHub repo owner/name used by the installer archive. Defaults to LOOPX_REPO or loopx-project/loopx.",
    )
    update_parser.add_argument(
        "--ref",
        help="Git ref used by the installer archive. Defaults to LOOPX_REF or stable.",
    )
    update_parser.add_argument(
        "--archive-url",
        help="Explicit tarball URL passed to the installer as LOOPX_ARCHIVE_URL.",
    )
    update_parser.add_argument(
        "--installed-doctor-json",
        help=(
            "Local JSON output from the installed `loopx --format json doctor`; "
            "valid only with --check for source-versus-installed qualification."
        ),
    )
    update_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Timeout for `update apply` installer and post-update doctor commands.",
    )


def handle_update_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    registry_was_supplied: bool,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int | None:
    if args.command not in UPDATE_CONTROL_COMMANDS:
        return None

    update_action = UpdateAction.PLAN
    try:
        update_action = resolve_update_action(
            args.update_action,
            check=args.check,
            dry_run=args.dry_run,
            execute=args.execute,
        )
        if args.rollback and args.update_action:
            raise ValueError(
                "update rollback cannot be combined with check, plan, or apply"
            )
        if args.installed_doctor_json and update_action is not UpdateAction.CHECK:
            raise ValueError("--installed-doctor-json requires `loopx update check`")
        if args.rollback:
            payload = build_rollback_plan(release_id=args.rollback)
            payload = execute_rollback_plan(
                payload, timeout_seconds=args.timeout_seconds
            )
        else:
            doctor_payload = None
            if args.installed_doctor_json:
                doctor_path = Path(args.installed_doctor_json).expanduser()
                loaded_doctor = json.loads(doctor_path.read_text(encoding="utf-8"))
                if not isinstance(loaded_doctor, dict):
                    raise ValueError(
                        "--installed-doctor-json must contain a JSON object"
                    )
                doctor_payload = loaded_doctor
            payload = build_update_plan(
                repo=args.repo,
                ref=args.ref,
                archive_url=args.archive_url,
                action=update_action,
                doctor_payload=doctor_payload,
            )
            payload["installed_doctor_source"] = (
                "explicit_json" if doctor_payload is not None else "current_runtime"
            )
            if update_action is UpdateAction.APPLY and payload.get("plan", {}).get(
                "apply_supported"
            ):
                from ..control_plane.heartbeat.installed_prompt_update import (
                    update_with_prompts,
                )
                payload = update_with_prompts(
                    payload, registry=(registry_path if registry_was_supplied else explicit_global_registry(args.runtime_root)),
                    runtime_root=args.runtime_root,
                    timeout_seconds=args.timeout_seconds, runtime_update=execute_update_plan,
                )
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_update_plan_v0",
            "mode": "update",
            "requested_action": update_action.value,
            "check_only": update_action is UpdateAction.CHECK,
            "dry_run": update_action is not UpdateAction.APPLY,
            "execute_requested": update_action is UpdateAction.APPLY,
            "changes_applied": False,
            "error": str(exc),
            "recommended_action": "fix update planning or installation before retrying",
        }
    print_payload(payload, output_format(args), render_update_plan_markdown)
    return 0 if payload.get("ok") else 1
