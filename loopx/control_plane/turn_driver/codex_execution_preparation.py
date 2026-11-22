"""Managed Host lease preparation and the Codex lifecycle-hook adapter.

The TypeScript control plane remains the decision owner.  This adapter accepts
only its exact ordinary claim, executes it through the public CLI, and proves
the captured Todo and Turn are ready before returning a Host receipt.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ..quota.effect_program import SettlementIdentity

from ...command_invocation import command_argv, current_loopx_cli_argv
from ...turn_identity import mint_turn_instance_id, normalize_turn_instance_id
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


class NativeRecoveryLeaseUnavailable(RuntimeError):
    """The original lease could not authorize recovery publication."""


class NativePreparationTurnUnavailable(ValueError):
    """The original Turn cannot authorize a new host claim."""


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
    goal_ref = payload.get("goal_ref")
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
        **({"goal_ref": dict(goal_ref)} if isinstance(goal_ref, Mapping) else {}),
        **claim_binding,
    }


def _default_runner(
    command: Sequence[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command), cwd=str(cwd), capture_output=True, text=False,
        timeout=timeout,
        check=False,
    )
    # The receipt is protocol data and must decode exactly.  Stderr is only
    # diagnostic output from the launcher; an unrelated invalid byte there
    # must not turn a valid receipt into a failed preparation.
    stdout = (completed.stdout or b"").decode("utf-8", errors="strict")
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout,
        stderr,
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
    route = current_loopx_cli_argv()
    command = command_argv(
        Path(route[0]),
        [
            *route[1:],
            "--registry", str(registry_path),
            *(["--runtime-root", str(runtime_root)] if runtime_root is not None else []),
            "--format", "json",
            *arguments,
        ],
    )
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


class ExecutionPreparationClaimFailed(ValueError):
    """The decision's advertised managed claim was refused before delivery."""

    code = "turn_execution_preparation_claim_failed"
    execution_effects = {
        "host_invoked": False, "state_written": None,
        "scheduler_acknowledged": False, "quota_spent": False,
    }


class UnconsumedExecutionPreparationClaim(ValueError):
    """The builtin adapter could not prepare a required managed claim."""

    code = "turn_execution_preparation_claim_not_consumed"

    def __init__(self, recovery: Mapping[str, str]) -> None:
        super().__init__("The builtin CLI did not claim the Todo required to run this Turn.")
        self.payload = {"status": "execution_preparation_required", "executed": False, **recovery}


def _exact_identity(value: object) -> str | None:
    """Keep a decision identity exact; display normalization cannot stand in for it."""
    if isinstance(value, str) and value and value == value.strip():
        return value
    return None


def _builtin_claim_identity(
    decision: Mapping[str, Any], agent_id: str,
) -> tuple[str, str, str, list[str]] | None:
    preparation = decision.get("execution_preparation")
    selected = decision.get("selected_todo")
    if not isinstance(preparation, Mapping) or not isinstance(selected, Mapping):
        return None
    if preparation.get("state") != "claim_required":
        return None
    claim = preparation.get("claim")
    if not isinstance(claim, Mapping) or claim.get("host_execution_allowed") is not True:
        return None
    identity = decision.get("agent_identity")
    declared_agent = decision.get("agent_id")
    selected_agent = selected.get("agent_id")
    identity_agent = identity.get("agent_id") if isinstance(identity, Mapping) else None
    goal_id = _exact_identity(decision.get("goal_id"))
    caller_agent_id = _exact_identity(agent_id)
    preparation_agent_id = _exact_identity(preparation.get("agent_id"))
    todo_id = _exact_identity(preparation.get("todo_id"))
    selected_todo_id = _exact_identity(selected.get("todo_id"))
    selected_agent_id = _exact_identity(selected_agent)
    identity_agent_id = _exact_identity(identity_agent)
    # An absent declaration may fall back to the composed identity contract.
    # A present declaration that needed trimming is not the same identity.
    decision_agent_id = (
        _exact_identity(declared_agent) if declared_agent is not None else identity_agent_id
    )
    if (
        preparation.get("schema_version") != "execution_preparation_v0"
        or preparation.get("delivery_allowed") is not False
        or decision.get("normal_delivery_allowed") is not False
        or decision.get("should_run") is not True
        or not goal_id
        or not caller_agent_id
        or not decision_agent_id
        or not preparation_agent_id
        or not todo_id
        or selected_todo_id != todo_id
        or decision_agent_id != caller_agent_id
        or preparation_agent_id != caller_agent_id
        or (selected_agent is not None and selected_agent_id != caller_agent_id)
        or (identity_agent is not None and identity_agent_id != caller_agent_id)
    ):
        return None
    expected_claim = [
        "todo", "claim", "--goal-id", goal_id, "--todo-id", todo_id,
        "--agent-id", caller_agent_id, "--claimed-by", caller_agent_id,
    ]
    if claim.get("cli_args") != expected_claim:
        return None
    return goal_id, caller_agent_id, todo_id, expected_claim


def builtin_execution_preparation_claim(
    decision: Mapping[str, Any],
    *,
    agent_id: str,
    registry_path: Path,
    runtime_root: Path,
    cwd: Path,
    turn_instance_id: str | None = None,
    goal_ref: Mapping[str, object] | None = None,
    runner: CommandRunner | None = None,
    timeout: float = 30,
    admit_start: Callable[[SettlementIdentity], bool] | None = None,
    on_claim: Callable[[Mapping[str, Any]], None] | None = None,
) -> str | None:
    """Execute the exact ordinary claim a decision advertised for a Host.

    The decision stays the owner of readiness: this adapter runs only the
    claim the decision already published as Host-executable, then the caller
    re-reads the same decision and must observe the same Todo ready.
    """
    claim_identity = _builtin_claim_identity(decision, agent_id)
    if claim_identity is None or not all(path.is_absolute() for path in (registry_path, runtime_root, cwd)):
        return None
    goal_id, caller_agent_id, todo_id, expected_claim = claim_identity
    heartbeat = decision.get("heartbeat_receipt")
    # Resolve one Turn identity before reading a receipt or running the claim.
    # The transaction plan applies the same normalization, so a padded or
    # invalid identity must be refused here; otherwise the claim could lease a
    # different Todo before planning rejects the mismatch.
    exact_turn_instance_id = normalize_turn_instance_id(turn_instance_id)
    if exact_turn_instance_id is None and isinstance(heartbeat, Mapping):
        receipt_turn_instance_id = heartbeat.get("turn_instance_id")
        if isinstance(receipt_turn_instance_id, str):
            exact_turn_instance_id = normalize_turn_instance_id(
                receipt_turn_instance_id
            )
    if exact_turn_instance_id is None and admit_start is not None:
        exact_turn_instance_id = mint_turn_instance_id(prefix="turn")
    if exact_turn_instance_id is not None:
        # The receipt lookup and the ordinary claim are separate operations.
        # Reserve the exact Turn binding before the claim can create a lease for
        # a different Todo. The reservation owner serializes this write with every
        # later guard, and planning re-reads the same identity after the claim.
        from ..quota.effect_program import SettlementBindingKind, SettlementIdentity
        from ..quota.heartbeat_receipt import ensure_turn_heartbeat_settlement_receipt
        from ..quota.turn_envelope import turn_envelope_scoped_settlement_identity

        raw_identity = turn_envelope_scoped_settlement_identity(
            decision, turn_instance_id=exact_turn_instance_id,
        )
        # A blocked pre-claim decision has no completed transaction to project.
        # The ordinary Todo claim already fixes the remaining identity fields,
        # so derive the same settlement binding directly and compare it with
        # the receipt before the claim can create a lease.
        settlement_identity = (
            SettlementIdentity.from_runtime_payload(raw_identity)
            if raw_identity is not None
            else SettlementIdentity(
                goal_id=goal_id,
                agent_id=caller_agent_id,
                todo_id=todo_id,
                turn_instance_id=exact_turn_instance_id,
            )
        )
        if (
            settlement_identity.goal_id != goal_id
            or settlement_identity.agent_id != caller_agent_id
            or settlement_identity.turn_instance_id != exact_turn_instance_id
            or settlement_identity.binding_kind is not SettlementBindingKind.TODO
            or settlement_identity.binding_id != todo_id
        ):
            raise ValueError("execution preparation identity does not match its ordinary claim")
        class StartDeferred(Exception):
            pass

        def before_bind() -> None:
            from ..quota.settlement import read_quota_spend_precheck

            original = read_quota_spend_precheck(
                runtime_root, goal_id=goal_id, agent_id=caller_agent_id, todo_id=todo_id,
                turn_instance_id=exact_turn_instance_id, allow_unbound_binding=True,
                registry_path=registry_path, goal_ref=goal_ref,
            )
            if original is not None and original.spend_run_present:
                raise ValueError(
                    "This Turn is already settled; use --resume-turn-instance-id "
                    f"{exact_turn_instance_id} to read its confirmed journal before new work"
                )
            if admit_start is not None and not admit_start(settlement_identity):
                raise StartDeferred
        # The receipt owner serializes this reservation with every later guard
        # binding.  Reserving the Turn before the ordinary claim closes the
        # check-to-claim race: a conflicting Todo either wins here or is refused
        # here, so the losing caller cannot acquire a lease first.
        try:
            ensure_turn_heartbeat_settlement_receipt(
                runtime_root,
                settlement_identity,
                semantic_replan_guard_scoped=False,
                semantic_replan_obligation_id=None,
                prepared_lease_required=True,
                goal_ref=goal_ref,
                **({"before_bind": before_bind} if admit_start is not None else {}),
            )
        except StartDeferred:
            return None
    try:
        claim_receipt = _run_json(
            expected_claim,
            registry_path=registry_path,
            runtime_root=runtime_root,
            cwd=cwd,
            timeout=timeout,
            runner=runner or _default_runner,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeDecodeError) as exc:
        raise ExecutionPreparationClaimFailed(
            f"managed claim for Todo {todo_id} was refused before delivery: {exc}"
        ) from exc
    if on_claim is not None:
        on_claim(claim_receipt)
    return todo_id


def unconsumed_preparation_refusal(
    decision: Mapping[str, Any], *, goal_id: str, agent_id: str,
) -> UnconsumedExecutionPreparationClaim | None:
    """Surface only a claim the exact builtin adapter could accept."""
    preparation = decision.get("execution_preparation")
    if not isinstance(preparation, Mapping) or preparation.get("state") != "claim_required":
        return None
    recovery: dict[str, str] = {}
    identity = _builtin_claim_identity(decision, agent_id)
    if identity is not None and identity[0] == goal_id:
        _, _, todo_id, argv = identity
        command = shlex.join(["loopx", *argv, "--thin"])
        if preparation["claim"].get("command_line") == command:
            recovery = {
                "todo_id": todo_id,
                "claim_command": command,
                "next_step": (
                    "Use the same registry and runtime options for the claim. "
                    "Rerun quota should-run for the same Goal, agent, Todo, and Turn, "
                    "and continue only when execution_preparation.state is ready."
                ),
            }
    return UnconsumedExecutionPreparationClaim(recovery)


def _native_claim_operation_id(request: Mapping[str, Any]) -> str:
    """Reuse the captured managed claim identity on an authorized retry."""
    digest = request.get("claim_source_sha256")
    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError("Native execution preparation requires its original capture digest")
    return f"todo-claim:codex-capture:{digest}"


def _native_context_argv(scenario: str, directory: Path, *, progressive_enabled: bool) -> list[str]:
    return [
        *(["--codex-app"] if scenario == "codex_app_heartbeat" else ["--runtime-profile", scenario]),
        *(["--context-delivery", "on"] if progressive_enabled else
          ["--context-delivery", "off", "--delta-delivery", "auto"]),
        "--codex-context-dir", str(directory),
    ]


def _native_readiness_guard(
    goal: str, agent: str, todo: str, turn: str, *, context_argv: Sequence[str],
    goal_ref: Mapping[str, Any] | None = None,
) -> list[str]:
    instance = []
    if goal_ref is not None:
        instance_id = _exact_identity(goal_ref.get("goal_instance_id"))
        if goal_ref.get("goal_id") != goal or instance_id is None:
            raise ValueError("Native readiness requires the original Goal instance.")
        instance = ["--goal-instance-id", instance_id]
    return [
        "quota", "should-run", "--goal-id", goal, "--agent-id", agent,
        *context_argv, "--defer-context-presentation",
        "--todo-id", todo, "--turn-instance-id", turn,
        *instance,
    ]



def require_native_recovery_lease(
    payload: Mapping[str, Any], *, claim_operation_id: str,
    registry_path: Path, runtime_root: Path,
) -> None:
    """Read the original claim and its current lease before publishing ready context."""
    from ..coordination.local_authority import local_authority_is_promoted
    from ..effect_runtime import CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS, effect_runtime_result
    from ..work_items.task_lease import inspect_task_lease

    goal = payload.get("goal_id")
    preparation = payload.get("execution_preparation")
    if (not isinstance(preparation, Mapping) or payload.get("normal_delivery_allowed") is not True
            or preparation.get("schema_version") != "execution_preparation_v0"
            or preparation.get("state") != "ready" or preparation.get("delivery_allowed") is not True
            or not _exact_identity(goal)
            or payload.get("registry") != str(registry_path)
            or payload.get("runtime_root") != str(runtime_root)
            or not claim_operation_id.startswith("todo-claim:codex-capture:")
            or not BARE_SHA256_PATTERN.fullmatch(claim_operation_id.removeprefix("todo-claim:codex-capture:"))):
        raise ValueError("Native readiness recovery requires the original bound claim.")

    agent, todo = preparation.get("agent_id"), preparation.get("todo_id")
    proof = preparation.get("lease_proof")
    if (preparation.get("handoff_mode") != "hard_lease"
            or not all(_exact_identity(part) for part in (agent, todo))
            or not isinstance(proof, Mapping)
            or not _exact_identity(proof.get("idempotency_key"))
            or type(proof.get("expected_version")) is not int
            or not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal)):
        raise ValueError("Native readiness recovery requires an authoritative lease proof.")

    def matches(lease: object) -> bool:
        return (isinstance(lease, Mapping)
                and lease.get("status") == "active"
                and (lease.get("goal_id"), lease.get("todo_id"), lease.get("owner")) == (goal, todo, agent)
                and lease.get("idempotency_key") == proof["idempotency_key"]
                and lease.get("version") == proof["expected_version"])

    original = effect_runtime_result("coordination.local_authority.operation_receipt", {
        "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
        "runtime_root": str(runtime_root), "goal_id": goal, "operation_id": claim_operation_id,
    }, timeout=CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS)
    records = original.get("receipts") if isinstance(original, Mapping) and original.get("status") == "found" else None
    record = records[0] if isinstance(records, list) and len(records) == 1 else None
    result = record.get("result") if isinstance(record, Mapping) else None
    if (not isinstance(result, Mapping)
            or record.get("schema_version") != "loopx_coordination_todo_claim_receipt_v0"
            or record.get("goal_id") != goal or record.get("operation_id") != claim_operation_id
            or result.get("claim_operation_id") != claim_operation_id
            or result.get("execution_managed") is not True
            or not matches(result.get("lease"))):
        raise ValueError("Saved readiness does not match the original claim receipt.")

    current = inspect_task_lease(registry_path=registry_path, runtime_root=runtime_root, goal_id=goal, todo_id=todo)
    if current.get("active") is not True or not matches(current.get("lease")):
        raise ValueError("The original task lease is no longer current for native readiness recovery.")


@contextmanager
def guard_native_recovery_publication(
    payload: Mapping[str, Any], *, claim_operation_id: str,
    registry_path: Path, runtime_root: Path,
) -> Iterator[None]:
    """Serialize the local journal commit with the canonical lease writer."""
    from ...file_lock import exclusive_mutation_file_lock
    from ..coordination.shadow_management import (
        require_shadow_primary_write_allowed,
        shadow_maintenance_lock_target,
    )

    goal = payload["goal_id"]
    with ExitStack() as locks:
        try:
            locks.enter_context(exclusive_mutation_file_lock(
                shadow_maintenance_lock_target(runtime_root, goal),
                operation="native-recovery-publication",
            ))
            require_shadow_primary_write_allowed(runtime_root, goal)
            require_native_recovery_lease(
                payload, claim_operation_id=claim_operation_id,
                registry_path=registry_path, runtime_root=runtime_root,
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise NativeRecoveryLeaseUnavailable(str(exc)) from exc
        yield


def codex_execution_preparation_readback_context(
    request: Mapping[str, Any], *, scenario: str, context_directory: str | None, progressive_enabled: bool,
) -> str | None:
    """Expose the original receipt and the continuation for its exact native Turn."""
    goal, agent, todo, turn = (request.get(key) for key in ("goal_id", "agent_id", "todo_id", "turn_instance_id"))
    route = request.get("authority_route")
    if (
        not isinstance(route, Mapping)
        or not all(_exact_identity(part) for part in (goal, agent, todo, turn))
        or normalize_turn_instance_id(turn) != turn
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
    guidance = (
        "If a verified ready capture is available, follow its recovery command after matching the original active lease. "
        "The ready projection checks the current lease before delivery. Follow its reader after success. "
    )
    ready_capture = request.get("ready_capture")
    if (
        ready_capture is None
        and request.get("readback_only") is True
        and scenario in {"codex_app_heartbeat", "codex_app_ssh_goal"}
        and context_directory is not None
        and (session_directory := Path(context_directory)).is_absolute()
        and operation_id == _native_claim_operation_id(request)
    ):
        # A lost readiness result must not replace the live session's claim
        # source if the fresh guard finds a retired lease or a different gate.
        # The existing capture writer creates this private staging directory.
        staged = session_directory / f"readiness-{uuid4().hex}"
        guard_context = _native_context_argv(scenario, staged, progressive_enabled=progressive_enabled)
        goal_ref = request.get("goal_ref")
        facts["readiness_command"] = shlex.join([
            *base, *_native_readiness_guard(goal, agent, todo, turn, context_argv=guard_context,
                goal_ref=goal_ref if isinstance(goal_ref, Mapping) else None),
            "--decision-output-dir", str(staged), "--prepared-capture-reference",
        ])
        facts["ready_context_command_template"] = shlex.join([
            *base, "quota", "context-project", "--goal-id", goal, "--agent-id", agent,
            "--turn-instance-id", turn, "--capture-file", str(staged / "decision.json"),
            "--capture-sha256", "<capture_sha256>", "--prepared-claim-operation-id", operation_id,
            *_native_context_argv(scenario, session_directory, progressive_enabled=progressive_enabled),
        ])
        guidance = (
            "Run readiness_command once from the original workspace after the original claim and active lease "
            "match in Goal, Todo, owner, key and version. Require normal_delivery_allowed=true and "
            "execution_preparation.state=ready for this same Goal, Agent, Todo and Turn, with lease_proof "
            "matching the original key and version. Only then substitute the returned capture_sha256 for "
            "<capture_sha256> in this receipt's ready_context_command_template. The projection rechecks the lease. "
            "Follow the returned reader before work. "
        )
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
                    )
                else:
                    # Re-open the exact ready source through its checked path.
                    # An outer digest alone does not validate a complete Turn.
                    recovery_command = shlex.join([
                        *base, "quota", "context-project", "--capture-file", str(path),
                        "--capture-sha256", ready_sha256, "--goal-id", goal, "--agent-id", agent,
                        "--turn-instance-id", turn, "--context-delivery", "off", "--delta-delivery", "off",
                        "--prepared-claim-operation-id", operation_id,
                    ])
                facts["capture_sha256"] = ready_sha256
                facts["context"] = {
                    "capture_sha256": ready_sha256,
                    "recovery_command": recovery_command,
                }
    return (
        "Automatic preparation started for this captured Turn and may still be finishing. "
        "Read the original claim receipt and current lease before deciding how to recover. "
        f"{guidance}"
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
    progressive_enabled: bool = True,
    timeout: float = 8,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Execute one journal-bound claim and publish its ready capture.

    The hook never selects work.  Unknown/malformed requests fail before a
    subprocess.  The ordinary claim remains the authority and rechecks all
    ownership, lease-generation, workspace and write-scope constraints.
    """
    from ..quota.accounting_admission import quota_accounting_admission
    from ..quota.effect_program import ReceiptBoundReplayPhase
    from ..quota.settlement import read_heartbeat_settlement

    goal_id = str(request.get("goal_id") or "").strip()
    agent_id = str(request.get("agent_id") or "").strip()
    todo_id = str(request.get("todo_id") or "").strip()
    turn_instance_id = str(request.get("turn_instance_id") or "").strip()
    goal_ref = request.get("goal_ref")
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
    if goal_ref is not None and not isinstance(goal_ref, Mapping):
        raise ValueError("invalid Codex execution-preparation Goal instance")
    guard = [
        *_native_readiness_guard(
            goal_id, agent_id, todo_id, turn_instance_id,
            context_argv=_native_context_argv(scenario, context_directory, progressive_enabled=progressive_enabled),
            goal_ref=goal_ref,
        ),
        "--decision-output-dir", str(capture_directory), "--prepared-capture-reference",
    ]
    invoke = runner or _default_runner
    deadline = monotonic() + timeout
    # Use the same index fence as writeback and spend. An immutable pre-claim
    # capture can outlive a manually settled Turn and its released lease.
    with quota_accounting_admission(
        runtime_root=runtime_root, registry_path=registry_path, goal_id=goal_id,
        goal_ref=goal_ref, operation="codex-native-execution-preparation",
        handoff_legacy_index=True,
    ) as admission:
        original = read_heartbeat_settlement(
            runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            turn_instance_id=turn_instance_id, registry_path=registry_path,
            goal_ref=goal_ref, source_admission=admission,
            borrow_source_admission=admission is not None, include_record_bodies=False,
        )
        if original is None or original.identity.value is None:
            raise NativePreparationTurnUnavailable(
                "Automatic preparation needs the original bound Turn receipt. Read that receipt before continuing."
            )
        if original.replay_phase is not ReceiptBoundReplayPhase.OPEN:
            raise NativePreparationTurnUnavailable(
                "The original Turn is already settling or settled. Read its receipt for any remaining settlement. "
                "New work requires a fresh eligible Turn."
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Native execution preparation deadline elapsed before the claim")
        claimed = _run_json(
            expected_claim, registry_path=registry_path, runtime_root=runtime_root,
            cwd=workspace, timeout=min(timeout / 2, remaining), runner=invoke,
        )
    claim_reference = claimed.get("claim_operation_id")
    if claim_reference != claim_operation_id:
        raise RuntimeError("Native execution preparation did not recover its original managed claim")
    # Reserve time for readiness and give it the time the claim left unused.
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Native execution preparation deadline elapsed before the ready check")
    after = _run_json(
        guard,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=workspace,
        timeout=remaining,
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
