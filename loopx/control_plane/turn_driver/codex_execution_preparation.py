"""Codex lifecycle-hook executor for the typed execution-preparation action.

The TypeScript control plane remains the decision owner.  This adapter accepts
only its exact ordinary claim, executes it through the public CLI, and proves
the same Todo (and Turn when present) is ready before returning a Host receipt.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...command_invocation import current_loopx_cli_argv


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def compact_execution_preparation_request(
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Retain only the exact Host-executable action beside a private capture."""

    preparation = payload.get("execution_preparation")
    selected = payload.get("selected_todo")
    if not isinstance(preparation, Mapping) or not isinstance(selected, Mapping):
        return None
    claim = preparation.get("claim")
    goal_id = str(payload.get("goal_id") or "").strip()
    agent_id = str(preparation.get("agent_id") or "").strip()
    todo_id = str(preparation.get("todo_id") or "").strip()
    selected_todo_id = str(selected.get("todo_id") or "").strip()
    registry_path = Path(str(payload.get("registry") or "")).expanduser()
    runtime_root = Path(str(payload.get("runtime_root") or "")).expanduser()
    if (
        preparation.get("schema_version") != "execution_preparation_v0"
        or preparation.get("state") != "claim_required"
        or not isinstance(claim, Mapping)
        or claim.get("host_execution_allowed") is not True
        or not goal_id
        or not agent_id
        or not todo_id
        or selected_todo_id != todo_id
        or not registry_path.is_absolute()
        or registry_path.resolve() != registry_path
        or not runtime_root.is_absolute()
        or runtime_root.resolve() != runtime_root
    ):
        return None
    claim_args = claim.get("cli_args")
    expected_claim = [
        "todo", "claim", "--goal-id", goal_id, "--todo-id", todo_id,
        "--agent-id", agent_id, "--claimed-by", agent_id,
    ]
    if claim_args != expected_claim:
        return None
    receipt = payload.get("heartbeat_receipt")
    turn_instance_id = (
        str(receipt.get("turn_instance_id") or "").strip()
        if isinstance(receipt, Mapping)
        else ""
    )
    return {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "claim_args": expected_claim,
        # The lifecycle hook can run from any project.  Retain the exact route
        # that produced this digest-bound action instead of rediscovering Goal
        # authority from the hook process cwd.
        "authority_route": {
            "registry_path": str(registry_path),
            "runtime_root": str(runtime_root),
        },
        **({"turn_instance_id": turn_instance_id} if turn_instance_id else {}),
    }


def _default_runner(
    command: Sequence[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        check=False,
    )


def _run_json(
    arguments: Sequence[str],
    *,
    registry_path: Path,
    runtime_root: str | Path | None,
    cwd: Path,
    timeout: float,
    runner: CommandRunner,
) -> dict[str, Any]:
    command = [
        *current_loopx_cli_argv(),
        "--registry", str(registry_path),
        *(["--runtime-root", str(runtime_root)] if runtime_root is not None else []),
        "--format", "json",
        *arguments,
    ]
    completed = runner(command, cwd=cwd, timeout=timeout)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("LoopX Host preparation returned non-JSON output") from exc
    if (
        completed.returncode != 0
        or not isinstance(payload, dict)
        or payload.get("ok") is False
    ):
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise RuntimeError(str(reason or completed.stderr or "LoopX Host preparation failed"))
    return payload


def execute_codex_execution_preparation(
    request: Mapping[str, Any],
    *,
    cwd: Path,
    scenario: str,
    timeout: float = 8,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Execute one projected claim and prove same-identity readiness.

    The hook never selects work.  Unknown/malformed requests fail before a
    subprocess.  The ordinary claim remains the authority and rechecks all
    ownership, lease-generation, workspace and write-scope constraints.
    """

    goal_id = str(request.get("goal_id") or "").strip()
    agent_id = str(request.get("agent_id") or "").strip()
    todo_id = str(request.get("todo_id") or "").strip()
    turn_instance_id = str(request.get("turn_instance_id") or "").strip()
    authority_route = request.get("authority_route")
    registry_path = Path(
        str(authority_route.get("registry_path") or "")
        if isinstance(authority_route, Mapping)
        else ""
    ).expanduser()
    runtime_root = Path(
        str(authority_route.get("runtime_root") or "")
        if isinstance(authority_route, Mapping)
        else ""
    ).expanduser()
    expected_claim = [
        "todo", "claim", "--goal-id", goal_id, "--todo-id", todo_id,
        "--agent-id", agent_id, "--claimed-by", agent_id,
    ]
    if (
        not goal_id
        or not agent_id
        or not todo_id
        or request.get("claim_args") != expected_claim
        or not registry_path.is_absolute()
        or registry_path.resolve() != registry_path
        or not runtime_root.is_absolute()
        or runtime_root.resolve() != runtime_root
        or scenario not in {"codex_app_heartbeat", "codex_app_ssh_goal"}
    ):
        raise ValueError("invalid Codex execution-preparation request")
    workspace = Path(cwd)
    if (
        not workspace.is_absolute()
        or not workspace.is_dir()
        or workspace.resolve() != workspace
    ):
        raise ValueError("Codex execution preparation requires a physical workspace")
    invoke = runner or _default_runner
    _run_json(
        expected_claim,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=workspace,
        timeout=timeout / 2,
        runner=invoke,
    )
    guard = [
        "quota", "should-run",
        "--goal-id", goal_id,
        "--agent-id", agent_id,
        *( ["--codex-app"] if scenario == "codex_app_heartbeat" else
           ["--runtime-profile", "codex_app_ssh_goal"] ),
        "--context-delivery", "off",
        "--todo-id", todo_id,
        *(["--turn-instance-id", turn_instance_id] if turn_instance_id else []),
    ]
    after = _run_json(
        guard,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=workspace,
        timeout=timeout / 2,
        runner=invoke,
    )
    preparation = after.get("execution_preparation")
    selected = after.get("selected_todo")
    receipt = after.get("heartbeat_receipt")
    same_turn = not turn_instance_id or (
        isinstance(receipt, Mapping)
        and receipt.get("turn_instance_id") == turn_instance_id
    )
    ready = (
        after.get("should_run") is True
        and after.get("normal_delivery_allowed") is True
        and isinstance(selected, Mapping)
        and selected.get("todo_id") == todo_id
        and isinstance(preparation, Mapping)
        and preparation.get("state") == "ready"
        and preparation.get("delivery_allowed") is True
        and same_turn
    )
    if not ready:
        raise RuntimeError(
            "LoopX Host claim completed without a same-Todo ready postcondition"
        )
    return {
        "event": "host_execution_prepared",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        **({"turn_instance_id": turn_instance_id} if turn_instance_id else {}),
        "execution_preparation": dict(preparation),
        "normal_delivery_allowed": True,
    }


def codex_execution_preparation_context(receipt: Mapping[str, Any]) -> str:
    """Render the small Host receipt that supersedes an earlier stale guard."""

    return (
        "LoopX Host automatically restored the exact selected Todo lease and "
        "rechecked the same Todo/Turn before this model step. Treat the earlier "
        "claim_required state as superseded by this ready receipt; do not repeat "
        "its claim or guard solely for that lease state. Current gates and later "
        "settlement checks still apply. Receipt: "
        + json.dumps(dict(receipt), ensure_ascii=False, separators=(",", ":"))
    )
