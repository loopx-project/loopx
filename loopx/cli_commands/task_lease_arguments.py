"""CLI grammar and repair hints; the typed lease owner still admits effects."""
from __future__ import annotations

import argparse
from pathlib import Path


LEASE_OPTION_FIELDS = (
    ("--owner", "owner"), ("--idempotency-key", "idempotency_key"),
    ("--new-owner", "new_owner"), ("--new-idempotency-key", "new_idempotency_key"),
    ("--transfer-claim", "transfer_claim"), ("--ttl-seconds", "ttl_seconds"),
    ("--write-scope", "write_scopes"), ("--write-worktree", "write_worktree"),
    ("--expected-version", "expected_version"),
)
_IDENTITY_FIELDS = frozenset({"owner", "idempotency_key"})
LEASE_ACTION_FIELDS = {
    "acquire": _IDENTITY_FIELDS | {"ttl_seconds", "write_scopes", "write_worktree", "expected_version"},
    "renew": _IDENTITY_FIELDS | {"ttl_seconds", "expected_version"},
    "transfer": _IDENTITY_FIELDS | {"new_owner", "new_idempotency_key", "transfer_claim", "ttl_seconds", "expected_version"},
    "release": _IDENTITY_FIELDS | {"expected_version"},
    "inspect": frozenset(),
}


def _present(value: object) -> bool:
    # An explicit zero CAS is input, not an absent flag. Never weaken it.
    return value is not None and value is not False and value != "" and value != []


def _option_args(flag: str, value: object) -> list[str]:
    text = str(value)
    # A legal value may look like an option. Bind it explicitly so argparse
    # cannot reinterpret the repair's identity, route or repeated scope.
    return [f"{flag}={text}"] if text.startswith("-") else [flag, text]


class TaskLeaseArgumentError(ValueError):
    def __init__(self, *, missing: list[str], unsupported: list[str]) -> None:
        self.missing = missing
        self.unsupported = unsupported
        parts = []
        if missing:
            parts.append("requires " + ", ".join(missing))
        if unsupported:
            parts.append("unsupported: " + ", ".join(unsupported))
        super().__init__("task-lease arguments " + "; ".join(parts))

    def recovery(self, args: argparse.Namespace, *, registry_path: Path,
                 runtime_root_arg: str | None) -> dict[str, object]:
        cli_args = [*_option_args("--registry", registry_path), "--format", "json"]
        if runtime_root_arg is not None:
            cli_args.extend(_option_args("--runtime-root", runtime_root_arg))
        action = args.task_lease_command
        cli_args.extend(["task-lease", action, *_option_args("--goal-id", args.goal_id),
                         *_option_args("--todo-id", args.todo_id)])
        for flag, field in LEASE_OPTION_FIELDS:
            value = getattr(args, field, None)
            if field not in LEASE_ACTION_FIELDS[action] or not _present(value):
                continue
            if value is True:
                cli_args.append(flag)
            else:
                for item in value if isinstance(value, list) else [value]:
                    cli_args.extend(_option_args(flag, item))
        return {
            "command": f"loopx task-lease {action}", "cli_args": cli_args,
            "requires_flags": self.missing, "remove_flags": self.unsupported,
            "reason": "Review removed flags and supply missing values, then retry. "
                      "Identities and CAS are preserved; canonical lease checks still apply. "
                      "This repair is not admission or permission to execute work.",
        }


def validate_task_lease_arguments(args: argparse.Namespace) -> None:
    action = args.task_lease_command
    required = set() if action == "inspect" else set(_IDENTITY_FIELDS)
    if action in {"renew", "transfer", "release"}:
        required.add("expected_version")
    if action == "transfer":
        required.update({"new_owner", "new_idempotency_key"})
    missing = [flag for flag, field in LEASE_OPTION_FIELDS
               if field in required and not _present(getattr(args, field, None))]
    unsupported = [flag for flag, field in LEASE_OPTION_FIELDS
                   if field not in LEASE_ACTION_FIELDS[action]
                   and _present(getattr(args, field, None))]
    if missing or unsupported:
        raise TaskLeaseArgumentError(missing=missing, unsupported=unsupported)
