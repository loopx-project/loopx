"""Codex lifecycle-hook executor for the typed execution-preparation action.

The TypeScript control plane remains the decision owner.  This adapter accepts
only its exact ordinary claim, executes it through the public CLI, and proves
the captured Todo and Turn are ready before returning a Host receipt.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...command_invocation import current_loopx_cli_argv
from ..content_digest import BARE_SHA256_PATTERN


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class LegacyNativePreparationCapture(ValueError):
    """The capture cannot prove a live guard bound its claim."""

    def __init__(self) -> None:
        super().__init__(
            "This saved capture has no verified live guard binding for Host claims. Preserve any existing claim receipt. "
            "If preparation is still needed, run the current quota should-run for the original Goal, "
            "Agent, Todo and Turn with a fresh capture and follow its preparation only if eligible."
        )


def compact_execution_preparation_request(
    payload: Mapping[str, Any], *, claim_source_sha256: str | None = None,
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
    claim_binding = {}
    if turn_instance_id and claim_source_sha256 is not None:
        operation_id = f"todo-claim:codex-capture:{claim_source_sha256}"
        expected_claim += ["--claim-operation-id", operation_id]
        claim_binding["claim_source_sha256"] = claim_source_sha256
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
        **claim_binding,
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


def prepared_capture_reference(payload: Mapping[str, Any], digest: str) -> dict[str, Any]:
    """Carry only the guard fields the Host uses to verify its claimed Turn."""
    selected = payload.get("selected_todo")
    heartbeat = payload.get("heartbeat_receipt")
    return {
        **{key: payload.get(key) for key in (
            "ok", "goal_id", "should_run", "normal_delivery_allowed", "execution_preparation",
        )},
        "selected_todo": {"todo_id": selected.get("todo_id")} if isinstance(selected, Mapping) else None,
        "heartbeat_receipt": {"turn_instance_id": heartbeat.get("turn_instance_id")} if isinstance(heartbeat, Mapping) else None,
        "capture_sha256": digest,
    }


def _native_claim_operation_id(request: Mapping[str, Any]) -> str:
    """Reuse the captured managed claim identity on an authorized retry."""
    digest = request.get("claim_source_sha256")
    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError("Native execution preparation requires its original capture digest")
    return f"todo-claim:codex-capture:{digest}"


def codex_execution_preparation_readback_context(request: Mapping[str, Any]) -> str | None:
    """Expose only existing receipt, lease and ready-capture readers after an interrupted preparation."""
    goal, agent, todo, turn = (request.get(key) for key in ("goal_id", "agent_id", "todo_id", "turn_instance_id"))
    route = request.get("authority_route")
    if not isinstance(route, Mapping) or not all(
        isinstance(part, str) and part for part in (goal, agent, todo, turn)
    ):
        return None
    registry_path, runtime_root = route.get("registry_path"), route.get("runtime_root")
    if not all(isinstance(part, str) and Path(part).is_absolute() for part in (registry_path, runtime_root)):
        return None
    operation_id = request.get("claim_operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        operation_id = _native_claim_operation_id(request)
    base = [*current_loopx_cli_argv(), "--registry", registry_path,
            "--runtime-root", runtime_root, "--format", "json"]
    facts: dict[str, Any] = {
        "event": "host_execution_preparation_readback_required",
        "goal_id": goal, "agent_id": agent, "todo_id": todo, "turn_instance_id": turn,
        "claim_operation_id": operation_id,
        "receipt_command": shlex.join([*base, "todo", "receipt", "--goal-id", goal, "--operation-id", operation_id]),
        "lease_command": shlex.join([*base, "task-lease", "inspect", "--goal-id", goal, "--todo-id", todo]),
    }
    ready_capture = request.get("ready_capture")
    if isinstance(ready_capture, Mapping):
        ready_path = ready_capture.get("path")
        ready_sha256 = ready_capture.get("sha256")
        if (
            isinstance(ready_path, str)
            and ready_path
            and isinstance(ready_sha256, str)
            and BARE_SHA256_PATTERN.fullmatch(ready_sha256) is not None
        ):
            from ..quota.turn_envelope import is_legacy_saved_quota_decision
            from .decision_capture import context_capture_read_command, read_context_capture_file

            try:
                path, _, saved = read_context_capture_file(ready_path, ready_sha256, private=True)
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                pass
            else:
                if is_legacy_saved_quota_decision(saved):
                    recovery_command = context_capture_read_command(
                        path, ready_sha256, reader_argv=current_loopx_cli_argv(),
                        select=["/heartbeat_receipt"] if request.get("complete_turn") is True else None,
                    )
                else:
                    # Re-open the exact ready source through its checked path.
                    # An outer digest alone does not validate a complete Turn.
                    recovery_command = shlex.join([
                        *base, "quota", "context-project", "--capture-file", str(path),
                        "--capture-sha256", ready_sha256, "--goal-id", goal, "--agent-id", agent,
                        "--turn-instance-id", turn, "--context-delivery", "off", "--delta-delivery", "off",
                    ])
                facts["capture_sha256"] = ready_sha256
                facts["context"] = {
                    "capture_sha256": ready_sha256,
                    "recovery_command": recovery_command,
                }
    return (
        "Automatic preparation started for this captured Turn and may still be finishing. "
        "Read the original claim receipt and current lease before deciding how to recover. "
        "With the matching active lease, follow the current captured same-Todo and same-Turn readiness check before work. "
        "If the recovery command returns a projection, follow its reader after the projection succeeds. "
        "For an authorized retry of this claim, pass the original --claim-operation-id. "
        "A retired lease requires a fresh eligible capture. "
        "Receipt: " + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    )


def execute_codex_execution_preparation(
    request: Mapping[str, Any],
    *,
    cwd: Path,
    scenario: str,
    capture_directory: Path,
    context_directory: Path,
    timeout: float = 8,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Execute one journal-bound claim and publish its ready capture.

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
    source_sha256 = request.get("source_sha256")
    if (
        context_directory is None or capture_directory is None or not turn_instance_id
        or request.get("readback_only") is True
        or not isinstance(source_sha256, str) or BARE_SHA256_PATTERN.fullmatch(source_sha256) is None
    ):
        raise ValueError("Native execution preparation requires a bound captured Turn")
    claim_operation_id = _native_claim_operation_id(request)
    expected_claim += ["--claim-operation-id", claim_operation_id]
    if request.get("claim_args") != expected_claim:
        raise ValueError("invalid Codex execution-preparation claim arguments")
    invoke = runner or _default_runner
    claimed = _run_json(
        expected_claim,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=workspace,
        timeout=timeout / 2,
        runner=invoke,
    )
    claim_reference = claimed.get("claim_operation_id")
    if claim_reference != claim_operation_id:
        raise RuntimeError("Native execution preparation did not recover its original managed claim")
    guard = [
        "quota", "should-run",
        "--goal-id", goal_id,
        "--agent-id", agent_id,
        *( ["--codex-app"] if scenario == "codex_app_heartbeat" else
           ["--runtime-profile", "codex_app_ssh_goal"] ),
        "--context-delivery", "on", "--codex-context-dir", str(context_directory), "--defer-context-presentation",
        "--todo-id", todo_id, "--turn-instance-id", turn_instance_id,
        "--decision-output-dir", str(capture_directory), "--prepared-capture-reference",
        *(["--complete-turn"] if request.get("complete_turn") is True else []),
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
    ready = (
        after.get("ok") is True
        and after.get("goal_id") == goal_id
        and after.get("should_run") is True
        and after.get("normal_delivery_allowed") is True
        and isinstance(selected, Mapping)
        and selected.get("todo_id") == todo_id
        and isinstance(preparation, Mapping)
        and preparation.get("state") == "ready"
        and preparation.get("todo_id") == todo_id
        and preparation.get("agent_id") == agent_id
        and preparation.get("delivery_allowed") is True
        and isinstance(receipt, Mapping)
        and receipt.get("turn_instance_id") == turn_instance_id
    )
    if not ready:
        raise RuntimeError(
            "LoopX Host claim completed without a same-Todo ready postcondition"
        )
    digest = after.get("capture_sha256")
    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError("The guard did not return its published capture digest")
    # The caller presents this same complete decision without admitting again.
    return {
        "event": "host_execution_prepared",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "execution_preparation": dict(preparation),
        "normal_delivery_allowed": True,
        "capture_sha256": digest,
        "capture_path": str(capture_directory / "decision.json"),
        "claim_operation_id": claim_operation_id,
    }


def codex_execution_preparation_context(receipt: Mapping[str, Any]) -> str:
    """Render the small Host receipt that supersedes an earlier stale guard."""

    context = receipt.get("context")
    guidance = ""
    if isinstance(context, Mapping):
        if context.get("ok") is False:
            guidance = ("Ready context delivery failed. Follow the recovery instructions in this receipt. "
                        "Use verified context for this Turn before work. Retain the original claim reference. ")
        else:
            guidance = ("Read the ready context in this receipt for current work and settlement, "
                        "replacing the earlier pre-claim view. Retain its original claim reference "
                        "for managed progress or completion. A saved context is not proof of "
                        "model consumption. ")
    return (
        "LoopX Host automatically restored the exact selected Todo lease and "
        "rechecked the same Todo/Turn before this model step. Treat the earlier "
        "claim_required state as superseded by this ready receipt; do not repeat "
        "its claim or guard solely for that lease state. Current gates and later "
        "settlement checks still apply. "
        + guidance
        + "Receipt: "
        + json.dumps(dict(receipt), ensure_ascii=False, separators=(",", ":"))
    )
