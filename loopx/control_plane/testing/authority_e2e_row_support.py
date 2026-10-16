"""Shared row vocabulary and CLI helpers for the shared-goal-authority ladder.

Every row runner returns a ``RowOutcome`` or raises ``RowAssertionError``; the
helpers here drive the product only through ``run_cli`` and never import a
LoopX writer, so a row cannot become a second authority over the goal state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .authority_e2e_fixtures import GoalWorkspace, JsonObject, run_cli

AGENT_A = "agent-a"


AGENT_B = "agent-b"


class RowAssertionError(AssertionError):
    """A row invariant failed; the message is written to be public-safe."""


@dataclass(frozen=True)
class RowContext:
    """Per-row scratch root and the environment the row may consult."""

    root: Path
    environ: Mapping[str, str]


@dataclass(frozen=True)
class RowOutcome:
    """What a row runner returns when it did not raise."""

    status: str
    reason_code: str | None
    evidence: JsonObject


def passed(**evidence: object) -> RowOutcome:
    return RowOutcome(status="pass", reason_code=None, evidence=dict(evidence))


def unverified(reason_code: str, **evidence: object) -> RowOutcome:
    return RowOutcome(status="unverified", reason_code=reason_code, evidence=dict(evidence))


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RowAssertionError(message)


def sha256_hex(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def add_todo(
    workspace: GoalWorkspace,
    text: str,
    *,
    claimed_by: str | None = None,
) -> JsonObject:
    return run_cli(
        workspace,
        "todo",
        "add",
        "--goal-id",
        workspace.goal_id,
        "--role",
        "agent",
        "--text",
        text,
        "--task-class",
        "advancement_task",
        *(("--claimed-by", claimed_by) if claimed_by else ()),
    )


def acquire_lease(
    workspace: GoalWorkspace,
    *,
    todo_id: str,
    owner: str,
    idempotency_key: str,
) -> JsonObject:
    return run_cli(
        workspace,
        "task-lease",
        "acquire",
        "--goal-id",
        workspace.goal_id,
        "--todo-id",
        todo_id,
        "--owner",
        owner,
        "--idempotency-key",
        idempotency_key,
        "--ttl-seconds",
        "120",
    )


def lease_version(payload: Mapping[str, object], *, label: str) -> str:
    lease = payload.get("lease")
    expect(isinstance(lease, dict), f"{label} must return a lease record")
    assert isinstance(lease, dict)
    return str(int(lease["version"]))


__all__ = [
    "AGENT_A",
    "AGENT_B",
    "RowAssertionError",
    "RowContext",
    "RowOutcome",
    "acquire_lease",
    "add_todo",
    "expect",
    "lease_version",
    "passed",
    "sha256_hex",
    "unverified",
]
